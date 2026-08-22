"""src/yaesm/subcommand/checksubcommand.py."""

import argparse
import logging

from yaesm.backup import Backup
from yaesm.cli import parse_comma_separated
from yaesm.subcommand.subcommandbase import SubcommandBase

logger = logging.getLogger(__name__)


class CheckSubcommand(SubcommandBase):
    """Validate that all preconditions for a backup are met."""

    def main(self, backups: list[Backup], parsed_args: argparse.Namespace) -> int:
        if parsed_args.backup_names is not None:
            backups_by_name = {backup.name: backup for backup in backups}
            unknown_names = [
                name for name in parsed_args.backup_names if name not in backups_by_name
            ]
            if not parsed_args.backup_names:
                logger.error("no backup names specified")
                return 2
            if unknown_names:
                for name in unknown_names:
                    logger.error(f"no backup named '{name}' in config")
                return 2
            backups = [backups_by_name[name] for name in parsed_args.backup_names]
        checks_passed = True
        for backup in backups:
            results = backup.backend.check(backup)
            failed = [result for result in results if not result.passed]
            if not parsed_args.quiet:
                print(f"backup: {backup.name}")
                for result in results:
                    print(f"    {'PASS' if result.passed else 'FAIL'}  {result.description}")
            if failed:
                checks_passed = False
                if parsed_args.quiet:
                    print(f"backup: {backup.name}")
                for result in failed:
                    for error in result.errors:
                        print(f"    {error}")
        return 0 if checks_passed else 1

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "backup_names",
            nargs="?",
            default=None,
            metavar="BACKUP[,BACKUP...]",
            type=parse_comma_separated,
            help="names of specific backups to check (default: check all)",
        )
        parser.add_argument(
            "-q",
            "--quiet",
            action="store_true",
            help="only show failed checks",
        )
