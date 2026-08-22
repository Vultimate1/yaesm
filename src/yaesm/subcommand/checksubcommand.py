"""src/yaesm/subcommand/checksubcommand.py."""

import argparse
import logging

from yaesm.backup import Backup
from yaesm.subcommand.subcommandbase import SubcommandBase

logger = logging.getLogger(__name__)


class CheckSubcommand(SubcommandBase):
    """Validate that all preconditions for a backup are met."""

    def main(self, backups: list[Backup], parsed_args: argparse.Namespace) -> int:
        if parsed_args.backup_name:
            backups = [b for b in backups if b.name == parsed_args.backup_name]
            if not backups:
                logger.error(f"no backup named '{parsed_args.backup_name}' in config")
                return 2
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
            "backup_name",
            nargs="?",
            default=None,
            help="name of a specific backup to check (default: check all)",
        )
        parser.add_argument(
            "-q",
            "--quiet",
            action="store_true",
            help="only show failed checks",
        )
