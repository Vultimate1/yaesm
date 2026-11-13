"""Queries and shared selection of backup artifacts."""

import argparse
import enum
from datetime import datetime, time, timedelta, timezone, tzinfo

import yaesm.ty as ty
from yaesm.backup import Backup, BackupArtifact
from yaesm.config import Config
from yaesm.errors import YaesmValueError
from yaesm.scheduler import Scheduler


class ArtifactQueryError(YaesmValueError):
    """Raised when an artifact query is invalid."""


class ArtifactQuery:
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
        qualname="ArtifactQuery.Type",
    )

    def __init__(
        self,
        query: ty.Sequence[str],
        now: datetime | None = None,
        zone: tzinfo = timezone.utc,
    ) -> None:
        self.query = list(query)
        self.type: enum.Enum
        self.target: datetime | None = None
        self.start: datetime | None = None
        self.end: datetime | None = None
        now = datetime.now(timezone.utc) if now is None else now
        if now.tzinfo is None or now.utcoffset() is None:
            now = self._localize(now, zone, str(now))
        now = now.astimezone(zone).replace(second=0, microsecond=0)

        match self.query:
            case [] | ["all"]:
                self.type = self.Type.ALL
            case ["newest"]:
                self.type = self.Type.NEWEST
            case ["oldest"]:
                self.type = self.Type.OLDEST
            case [("after" | "before" | "closest") as query_type, value]:
                self.type = self.Type(query_type)
                self.target = self._parse_time(value, now, zone)
            case ["between", first, second]:
                self.type = self.Type.BETWEEN
                self.start, self.end = sorted(
                    (
                        self._parse_time(first, now, zone),
                        self._parse_time(second, now, zone),
                    )
                )
            case _:
                raise ArtifactQueryError(
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
                    artifact for artifact in artifacts if artifact.operation.instant > self.target
                ]
            case self.Type.BEFORE:
                assert self.target is not None
                return [
                    artifact for artifact in artifacts if artifact.operation.instant < self.target
                ]
            case self.Type.BETWEEN:
                assert self.start is not None and self.end is not None
                return [
                    artifact
                    for artifact in artifacts
                    if self.start <= artifact.operation.instant <= self.end
                ]
            case self.Type.CLOSEST:
                target = self.target
                assert target is not None
                return (
                    [
                        min(
                            artifacts,
                            key=lambda artifact: abs(artifact.operation.instant - target),
                        )
                    ]
                    if artifacts
                    else []
                )
            case _:
                raise AssertionError(f"unknown find query type: {self.type}")

    @staticmethod
    def select_artifacts(
        backup: Backup,
        artifacts: ty.Sequence[BackupArtifact],
        queries: ty.Sequence["ArtifactQuery"],
        schedules: ty.Sequence[str] = (),
    ) -> list[BackupArtifact]:
        """Filter artifacts by schedule and query, preserving order without duplicate matches."""
        if schedules:
            schedule_names = {
                name: schedule.name for schedule in backup.schedules for name in schedule.names
            }
            selected_schedules = {schedule_names.get(name, name) for name in schedules}
            artifacts = tuple(
                artifact
                for artifact in artifacts
                if artifact.operation.schedule_name in selected_schedules
            )
        matches = {artifact.name for query in queries for artifact in query.select(artifacts)}
        return [artifact for artifact in artifacts if artifact.name in matches]

    @staticmethod
    def _parse_time(value: str, now: datetime, zone: tzinfo) -> datetime:
        if value.startswith("now-") and value[-1:] in {"m", "h", "d"}:
            amount = value[4:-1]
            if amount.isascii() and amount.isdecimal() and not amount.startswith("0"):
                try:
                    seconds = {"m": 60, "h": 3600, "d": 86400}[value[-1]]
                    return now.astimezone(timezone.utc) - timedelta(seconds=int(amount) * seconds)
                except OverflowError as error:
                    raise ArtifactQueryError(f"invalid time: {value}") from error

        try:
            parsed = None
            if len(value) == 10:
                parsed = datetime.fromisoformat(value)
            if len(value) in {16, 17, 21, 22} and value[10] == "T" and 0 <= int(value[11:13]) <= 23:
                parsed = datetime.fromisoformat(value)
            if len(value) == 5 and value[2] == ":" and 0 <= int(value[:2]) <= 23:
                parsed = datetime.combine(now.date(), time.fromisoformat(value))
            if parsed is not None:
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    parsed = ArtifactQuery._localize(parsed, zone, value)
                return parsed.astimezone(timezone.utc)
        except ArtifactQueryError:
            raise
        except ValueError as error:
            raise ArtifactQueryError(f"invalid time: {value}") from error
        raise ArtifactQueryError(f"invalid time: {value}")

    @staticmethod
    def _localize(value: datetime, zone: tzinfo, original: str) -> datetime:
        candidates = {
            candidate.astimezone(timezone.utc): candidate
            for fold in (0, 1)
            if (candidate := value.replace(tzinfo=zone, fold=fold))
            .astimezone(timezone.utc)
            .astimezone(zone)
            .replace(tzinfo=None)
            == value
        }
        if len(candidates) != 1:
            reason = "ambiguous; include a UTC offset" if candidates else "does not exist"
            raise ArtifactQueryError(f"invalid time: {original} is {reason}")
        return next(iter(candidates.values()))


def add_query_arguments(parser: argparse.ArgumentParser) -> None:
    """Add shared query and schedule selection arguments."""
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


def query_artifacts(
    config: Config, arguments: argparse.Namespace
) -> ty.Iterator[tuple[Backup, tuple[BackupArtifact, ...]]]:
    """Yield each selected backup and its matching artifacts, newest first.

    Validate all queries before listing artifacts. Apply schedule filters before
    queries, combine query matches without duplicates, and default to all artifacts.
    """
    backups = config.backups_for_targets(*arguments.targets.names)
    raw_queries = ([arguments.query] if arguments.query else []) + arguments.additional_queries
    now = datetime.now(timezone.utc)
    zone = Scheduler.timezone(config)
    queries = tuple(ArtifactQuery(query, now, zone) for query in raw_queries or ((),))

    for backup in backups:
        artifacts = ArtifactQuery.select_artifacts(
            backup, backup.artifacts(), queries, arguments.schedules
        )
        yield backup, tuple(artifacts)
