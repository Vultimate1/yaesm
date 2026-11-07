"""The diff subcommand."""

import argparse

from yaesm.config import Config
from yaesm.errors import YaesmValueError
from yaesm.query import add_query_arguments
from yaesm.subcommand.subcommandbase import SubcommandBase, TargetSelectionMode


class DiffError(YaesmValueError):
    """Raised when backups cannot be compared."""


class DiffSubcommand(SubcommandBase):
    """Compare backups with other backups or directory trees."""

    target_selection = TargetSelectionMode.REQUIRED

    def main(self, config: Config, arguments: argparse.Namespace) -> int:
        """Run the comparison once diff support is implemented."""
        del config, arguments
        raise DiffError("diff is not implemented yet")

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Add shared backup selection arguments."""
        add_query_arguments(parser)
