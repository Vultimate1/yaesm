"""src/yaesm/subcommand/checksubcommand.py."""

import argparse

from yaesm.backup import Backup
from yaesm.logging import Logging
from yaesm.subcommand.subcommandbase import SubcommandBase


class CheckSubcommand(SubcommandBase):
    """The check subcommand validates that all preconditions for a backup are met."""

    def main(self, backups: list[Backup], parsed_args: argparse.Namespace) -> int:
        if parsed_args.backup_name:
            backups = [b for b in backups if b.name == parsed_args.backup_name]
            if not backups:
                Logging.get().error(f"no backup named '{parsed_args.backup_name}' in config")
                return 2
        checks_passed = True
        for backup in backups:
            errors = backup.backend.check(backup)
            if errors:
                checks_passed = False
                print(f"backup: {backup.name}")
                for err in errors:
                    print(f"    {err}")
        return 0 if checks_passed else 1

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "backup_name",
            nargs="?",
            default=None,
            help="name of a specific backup to check (default: check all)",
        )
