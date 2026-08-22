import argparse
import logging
import sys

from yaesm.backup import Backup
from yaesm.cli import parse_comma_separated
from yaesm.subcommand.subcommandbase import SubcommandBase
from yaesm.timeframe import ImmediateTimeframe

logger = logging.getLogger(__name__)


class BackupSubcommand(SubcommandBase):
    """Perform one or more manual backups."""

    def main(self, backups: list[Backup], parsed_args: argparse.Namespace) -> int:
        keep = parsed_args.keep if parsed_args.keep is not None else sys.maxsize
        if keep < 1:
            logger.error(f"--keep must be a positive integer, got {keep}")
            return 1

        backups_by_name = {backup.name: backup for backup in backups}
        unknown_names = [name for name in parsed_args.backup_names if name not in backups_by_name]
        if not parsed_args.backup_names:
            logger.error("no backup names specified")
            return 1
        if unknown_names:
            for name in unknown_names:
                logger.error(f"backup not found: {name}")
            return 1

        timeframe = ImmediateTimeframe(keep=keep)
        backups_succeeded = True
        for name in parsed_args.backup_names:
            backup = backups_by_name[name]
            logger.info(f"starting backup '{backup.name}'")
            try:
                backup.backend.do_backup(backup, timeframe)
            except Exception:
                logger.error(f"backup '{backup.name}' failed", exc_info=True)
                backups_succeeded = False
                continue
            logger.info(f"backup '{backup.name}' completed successfully")
        return 0 if backups_succeeded else 1

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "backup_names",
            metavar="BACKUP[,BACKUP...]",
            type=parse_comma_separated,
            help="names of backups from the config",
        )
        parser.add_argument(
            "--keep",
            type=int,
            default=None,
            help="maximum number of immediate backups to keep (default: unlimited)",
        )
