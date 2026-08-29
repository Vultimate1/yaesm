"""The find subcommand."""

import argparse
import enum
from datetime import datetime, time, timedelta

import yaesm.ty as ty
from yaesm.backup import Backup, BackupArtifact
from yaesm.config import Config
from yaesm.errors import YaesmValueError
from yaesm.subcommand.subcommandbase import SubcommandBase


class FindError(YaesmValueError):
    """Raised when backups cannot be searched."""


class FindQueryError(FindError):
    """Raised when a find query is invalid."""


class FindSubcommand(SubcommandBase):
    """Find existing backups by name, schedule, and time."""

    def main(self, config: Config, arguments: argparse.Namespace) -> int:
        backups = self._select_backups(config, arguments.backup_names)
        raw_queries = ([arguments.query] if arguments.query else []) + arguments.additional_queries
        now = datetime.now()
        queries = tuple(FindQuery(query, now) for query in raw_queries or ((),))

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

    @staticmethod
    def _select_backups(config: Config, names: tuple[str, ...]) -> tuple[Backup, ...]:
        if not names:
            raise FindError("no backup names specified")
        if unknown := tuple(name for name in names if name not in config.backups_by_name):
            label = "backup" if len(unknown) == 1 else "backups"
            raise FindError(f"unknown {label}: {', '.join(repr(name) for name in unknown)}")
        selected = (config.backups_by_name[name] for name in names)
        return tuple({backup.name: backup for backup in selected}.values())

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "backup_names",
            metavar="BACKUP[,BACKUP...]",
            type=lambda value: tuple(dict.fromkeys(filter(None, map(str.strip, value.split(","))))),
        )
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
            "--null",
            action="store_true",
            help="separate results with NUL bytes",
        )


class FindQuery:
    """Parse and apply a query to artifacts ordered newest to oldest."""

    Type = enum.Enum(
        "Type",
        {
            "ALL": "all",
            "NEWEST": "newest",
            "OLDEST": "oldest",
            "AFTER": "after",
            "BEFORE": "before",
            "BETWEEN": "between",
            "CLOSEST": "closest",
        },
        module=__name__,
        qualname="FindQuery.Type",
    )

    def __init__(self, query: ty.Sequence[str], now: datetime | None = None) -> None:
        self.query = list(query)
        self.type: enum.Enum
        self.target: datetime | None = None
        self.start: datetime | None = None
        self.end: datetime | None = None
        now = (datetime.now() if now is None else now).replace(second=0, microsecond=0)

        match self.query:
            case [] | ["all"]:
                self.type = self.Type.ALL
            case ["newest"]:
                self.type = self.Type.NEWEST
            case ["oldest"]:
                self.type = self.Type.OLDEST
            case [("after" | "before" | "closest") as query_type, value]:
                self.type = self.Type(query_type)
                self.target = self._parse_time(value, now)
            case ["between", first, second]:
                self.type = self.Type.BETWEEN
                self.start, self.end = sorted(
                    (self._parse_time(first, now), self._parse_time(second, now))
                )
            case _:
                raise FindQueryError(
                    "invalid query; expected all, newest, oldest, after TIME, before TIME, "
                    "between TIME TIME, or closest TIME"
                )

    def select(self, artifacts: ty.Sequence[BackupArtifact]) -> list[BackupArtifact]:
        """Return artifacts matching this query."""
        match self.type:
            case self.Type.ALL:
                return list(artifacts)
            case self.Type.NEWEST:
                return list(artifacts[:1])
            case self.Type.OLDEST:
                return list(artifacts[-1:])
            case self.Type.AFTER:
                assert self.target is not None
                return [
                    artifact
                    for artifact in artifacts
                    if artifact.operation.created_at > self.target
                ]
            case self.Type.BEFORE:
                assert self.target is not None
                return [
                    artifact
                    for artifact in artifacts
                    if artifact.operation.created_at < self.target
                ]
            case self.Type.BETWEEN:
                assert self.start is not None and self.end is not None
                return [
                    artifact
                    for artifact in artifacts
                    if self.start <= artifact.operation.created_at <= self.end
                ]
            case self.Type.CLOSEST:
                target = self.target
                assert target is not None
                return (
                    [
                        min(
                            artifacts,
                            key=lambda artifact: abs(artifact.operation.created_at - target),
                        )
                    ]
                    if artifacts
                    else []
                )
            case _:
                raise AssertionError(f"unknown find query type: {self.type}")

    @staticmethod
    def _parse_time(value: str, now: datetime) -> datetime:
        if value.startswith("now-") and value[-1:] in {"m", "h", "d"}:
            amount = value[4:-1]
            if amount.isascii() and amount.isdecimal() and not amount.startswith("0"):
                try:
                    seconds = {"m": 60, "h": 3600, "d": 86400}[value[-1]]
                    return now - timedelta(seconds=int(amount) * seconds)
                except OverflowError as error:
                    raise FindQueryError(f"invalid time: {value}") from error

        try:
            if len(value) == 10:
                return datetime.fromisoformat(value)
            if len(value) == 16 and value[10] == "T":
                return datetime.fromisoformat(value)
            if len(value) == 5 and value[2] == ":":
                return datetime.combine(now.date(), time.fromisoformat(value))
        except ValueError as error:
            raise FindQueryError(f"invalid time: {value}") from error
        raise FindQueryError(f"invalid time: {value}")
