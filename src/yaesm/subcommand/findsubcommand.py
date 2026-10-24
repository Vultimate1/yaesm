"""The find subcommand."""

import argparse
from datetime import datetime, timezone

from yaesm.config import Config
from yaesm.errors import YaesmValueError
from yaesm.query import FindQuery, FindQueryError
from yaesm.scheduler import Scheduler
from yaesm.subcommand.subcommandbase import SubcommandBase, TargetSelectionMode


class FindError(YaesmValueError):
    """Raised when backups cannot be searched."""


class FindSubcommand(SubcommandBase):
    """Find existing backups by name, schedule, and time."""

    target_selection = TargetSelectionMode.REQUIRED

    def main(self, config: Config, arguments: argparse.Namespace) -> int:
        backups = config.backups_for_targets(*arguments.targets.names)
        raw_queries = ([arguments.query] if arguments.query else []) + arguments.additional_queries
        now = datetime.now(timezone.utc)
        zone = Scheduler.timezone(config)
        try:
            queries = tuple(FindQuery(query, now, zone) for query in raw_queries or ((),))
        except FindQueryError as error:
            raise FindError("cannot search backups") from error

        for backup in backups:
            artifacts = backup.artifacts()
            if arguments.schedules:
                schedule_names = {
                    name: schedule.name for schedule in backup.schedules for name in schedule.names
                }
                schedules = {schedule_names.get(name, name) for name in arguments.schedules}
                artifacts = tuple(
                    artifact
                    for artifact in artifacts
                    if artifact.operation.schedule_name in schedules
                )
            matches = {artifact.name for query in queries for artifact in query.select(artifacts)}
            for artifact in artifacts:
                if artifact.name in matches:
                    print(
                        backup.destination.format_locator(artifact),
                        end="\0" if arguments.null else "\n",
                    )
        return 0

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("query", nargs="*", metavar="QUERY")
        parser.add_argument(
            "--query",
            "-q",
            dest="additional_queries",
            action="append",
            nargs="+",
            default=[],
            metavar="QUERY",
            help="add another query",
        )
        parser.add_argument(
            "--schedule",
            "--schedules",
            "-s",
            dest="schedules",
            action="extend",
            type=lambda value: tuple(dict.fromkeys(filter(None, map(str.strip, value.split(","))))),
            default=[],
            metavar="SCHEDULE[,SCHEDULE...]",
            help="limit results to schedules",
        )
        parser.add_argument(
            "-0",
            dest="null",
            action="store_true",
            help="separate results with NUL bytes",
        )
