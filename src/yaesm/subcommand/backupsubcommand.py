import argparse
import logging
import sys

from yaesm.backup import Backup
from yaesm.subcommand.subcommandbase import SubcommandBase
from yaesm.timeframe import ImmediateTimeframe

logger = logging.getLogger(__name__)


class BackupSubcommand(SubcommandBase):
    """Perform a single manual backup."""

    def main(self, backups: list[Backup], parsed_args: argparse.Namespace) -> int:
        backup = None
        for b in backups:
            if b.name == parsed_args.backup_name:
                backup = b
                break
        if backup is None:
            logger.error(f"backup not found: {parsed_args.backup_name}")
            return 1

        keep = parsed_args.keep if parsed_args.keep is not None else sys.maxsize
        if keep < 1:
            logger.error(f"--keep must be a positive integer, got {keep}")
            return 1
        timeframe = ImmediateTimeframe(keep=keep)

        logger.info(f"starting backup '{backup.name}'")
        try:
            backup.backend.do_backup(backup, timeframe)
        except Exception:
            logger.error(f"backup '{backup.name}' failed", exc_info=True)
            return 1

        logger.info(f"backup '{backup.name}' completed successfully")
        return 0

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("backup_name", help="name of the backup from the config")
        parser.add_argument(
            "--keep",
            type=int,
            default=None,
            help="maximum number of immediate backups to keep (default: unlimited)",
        )
