import argparse
import enum
import logging
import re
from collections.abc import Sequence
from datetime import datetime, time, timedelta

from yaesm.backup import Backup, BackupArtifact
from yaesm.subcommand.subcommandbase import SubcommandBase
from yaesm.timeframe import ImmediateTimeframe, tframe_types

logger = logging.getLogger(__name__)


class FindQueryError(ValueError): ...


class FindSubcommand(SubcommandBase):
    """Find existing backups by name, timeframe, and time."""

    def main(self, backups: list[Backup], parsed_args: argparse.Namespace) -> int:
        """Find backup artifacts matching the requested queries."""
        backups_by_name = {backup.name: backup for backup in backups}
        unknown_names = [name for name in parsed_args.backup_names if name not in backups_by_name]
        if not parsed_args.backup_names:
            logger.error("no backup names specified")
            return 2
        if unknown_names:
            for name in unknown_names:
                logger.error("no backup named '%s' in config", name)
            return 2

        raw_queries = (
            [parsed_args.query] if parsed_args.query else []
        ) + parsed_args.additional_queries
        now = datetime.now()
        try:
            queries = [FindQuery(raw_query, now=now) for raw_query in raw_queries or [[]]]
        except FindQueryError as exc:
            logger.error("query error: %s", exc)
            return 2

        for name in parsed_args.backup_names:
            backup = backups_by_name[name]
            timeframes = None
            if parsed_args.timeframes:
                timeframes_by_name = {timeframe.name: timeframe for timeframe in backup.timeframes}
                timeframes_by_name[ImmediateTimeframe.name] = ImmediateTimeframe(keep=1)
                timeframes = [
                    timeframes_by_name[name]
                    for name in parsed_args.timeframes
                    if name in timeframes_by_name
                ]

            artifacts = backup.backend.collect(backup, timeframes=timeframes)
            matches = {artifact for query in queries for artifact in query.select(artifacts)}
            for artifact in artifacts:
                if artifact in matches:
                    print(artifact.locator)

        return 0

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Add find arguments to the subcommand parser."""
        valid_timeframes = tframe_types(names=True)
        parser.add_argument(
            "backup_names",
            metavar="BACKUP[,BACKUP...]",
            type=lambda value: list(dict.fromkeys(filter(None, map(str.strip, value.split(","))))),
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
            "--timeframe",
            "--timeframes",
            "-t",
            dest="timeframes",
            action="extend",
            type=lambda value: [
                valid_timeframes[valid_timeframes.index(timeframe)]
                for timeframe in dict.fromkeys(filter(None, map(str.strip, value.split(","))))
            ],
            default=[],
            metavar="TIMEFRAME[,TIMEFRAME...]",
        )


class FindQuery:
    """Parse and apply a query to backup artifacts ordered newest to oldest."""

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

    def __init__(self, query: list[str], now: datetime | None = None) -> None:
        self.query = query.copy()
        self.type: enum.Enum
        self.target: datetime | None = None
        self.start: datetime | None = None
        self.end: datetime | None = None
        now = (datetime.now() if now is None else now).replace(second=0, microsecond=0)

        match query:
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
                first_time = self._parse_time(first, now)
                second_time = self._parse_time(second, now)
                self.start, self.end = sorted((first_time, second_time))
            case _:
                raise FindQueryError(
                    "invalid query; expected all, newest, oldest, after TIME, before TIME, "
                    "between TIME TIME, or closest TIME"
                )

    def select(self, artifacts: Sequence[BackupArtifact]) -> list[BackupArtifact]:
        """Return backup artifacts matching this query."""
        match self.type:
            case self.Type.ALL:
                return list(artifacts)
            case self.Type.NEWEST:
                return list(artifacts[:1])
            case self.Type.OLDEST:
                return list(artifacts[-1:])
            case self.Type.AFTER:
                assert self.target is not None
                return [artifact for artifact in artifacts if artifact.created_at > self.target]
            case self.Type.BEFORE:
                assert self.target is not None
                return [artifact for artifact in artifacts if artifact.created_at < self.target]
            case self.Type.BETWEEN:
                assert self.start is not None
                assert self.end is not None
                return [
                    artifact
                    for artifact in artifacts
                    if self.start <= artifact.created_at <= self.end
                ]
            case self.Type.CLOSEST:
                target = self.target
                assert target is not None
                return (
                    [
                        min(
                            artifacts,
                            key=lambda artifact: abs(artifact.created_at - target),
                        )
                    ]
                    if artifacts
                    else []
                )
            case _:
                raise AssertionError(f"unknown find query type: {self.type}")

    @staticmethod
    def _parse_time(value: str, now: datetime) -> datetime:
        date_pattern = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
        time_pattern = r"[0-9]{2}:[0-9]{2}"
        try:
            if match := re.fullmatch(r"now-([1-9][0-9]*)([mhd])", value):
                amount = int(match.group(1))
                seconds = {"m": 60, "h": 3600, "d": 86400}[match.group(2)]
                return now - timedelta(seconds=amount * seconds)
            if re.fullmatch(rf"{date_pattern}(?:T{time_pattern})?", value):
                return datetime.fromisoformat(value)
            if re.fullmatch(time_pattern, value):
                return datetime.combine(now.date(), time.fromisoformat(value))
        except (ValueError, OverflowError) as exc:
            raise FindQueryError(f"invalid time: {value}") from exc

        raise FindQueryError(f"invalid time: {value}")
