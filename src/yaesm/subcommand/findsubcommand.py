"""The find subcommand."""

import argparse

from yaesm.config import Config
from yaesm.errors import YaesmValueError
from yaesm.query import ArtifactQueryError, add_query_arguments, query_artifacts
from yaesm.subcommand.subcommandbase import SubcommandBase, TargetSelectionMode


class FindError(YaesmValueError):
    """Raised when backups cannot be searched."""


class FindSubcommand(SubcommandBase):
    """Find existing backups by name, schedule, and time."""

    target_selection = TargetSelectionMode.REQUIRED

    def main(self, config: Config, arguments: argparse.Namespace) -> int:
        try:
            for backup, artifacts in query_artifacts(config, arguments):
                for artifact in artifacts:
                    print(
                        backup.destination.format_locator(artifact),
                        end="\0" if arguments.null else "\n",
                    )
        except ArtifactQueryError as error:
            raise FindError("cannot search backups") from error
        return 0

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        add_query_arguments(parser)
        parser.add_argument(
            "-0",
            dest="null",
            action="store_true",
            help="separate results with NUL bytes",
        )
