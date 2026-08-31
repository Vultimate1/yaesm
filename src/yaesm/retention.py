"""Backup retention policies."""

import abc
import dataclasses
from datetime import timezone

import voluptuous as vlp

import yaesm.ty as ty
from yaesm.backup import BackupArtifact
from yaesm.errors import YaesmValueError


class RetentionPolicyBase(abc.ABC):
    """Base class for policies that select artifacts to retain."""

    @classmethod
    @abc.abstractmethod
    def name(cls) -> str:
        """Return the stable configuration name for this policy."""

    @staticmethod
    @abc.abstractmethod
    def config_schema() -> vlp.Schema:
        """Return the complete schema for this policy's configuration."""

    @abc.abstractmethod
    def retain(
        self, artifacts: ty.Sequence[BackupArtifact], now: ty.datetime
    ) -> list[BackupArtifact]:
        """Return the artifacts protected by this policy."""


@dataclasses.dataclass(frozen=True)
class KeepAll(RetentionPolicyBase):
    """Retain every artifact, optionally from one schedule."""

    schedule_name: str | None = None

    @classmethod
    def name(cls) -> str:
        return "keep-all"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})

    def retain(
        self, artifacts: ty.Sequence[BackupArtifact], now: ty.datetime
    ) -> list[BackupArtifact]:
        """Return every matching artifact, newest first."""
        return sorted(
            (
                artifact
                for artifact in artifacts
                if self.schedule_name is None
                or artifact.operation.schedule_name == self.schedule_name
            ),
            key=lambda artifact: artifact.operation.instant,
            reverse=True,
        )


@dataclasses.dataclass(frozen=True)
class KeepLast(RetentionPolicyBase):
    """Retain the newest artifacts, optionally from one schedule."""

    count: int
    schedule_name: str | None = None

    @classmethod
    def name(cls) -> str:
        return "keep-last"

    def __post_init__(self) -> None:
        if self.count < 1:
            raise YaesmValueError("count must be at least 1")

    @staticmethod
    def config_schema() -> vlp.Schema:
        def count(value: object) -> int:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise vlp.Invalid("count must be a positive integer")
            return value

        mapping = vlp.Schema({vlp.Required("count"): count})
        return vlp.Schema(
            lambda value: mapping(
                {"count": value}
                if isinstance(value, int) and not isinstance(value, bool)
                else value
            )
        )

    def retain(
        self, artifacts: ty.Sequence[BackupArtifact], now: ty.datetime
    ) -> list[BackupArtifact]:
        """Return the newest matching artifacts."""
        matching = (
            artifact
            for artifact in artifacts
            if self.schedule_name is None or artifact.operation.schedule_name == self.schedule_name
        )
        return sorted(matching, key=lambda artifact: artifact.operation.instant, reverse=True)[
            : self.count
        ]


@dataclasses.dataclass(frozen=True)
class KeepFor(RetentionPolicyBase):
    """Retain artifacts created within a duration, optionally from one schedule."""

    duration: ty.timedelta
    schedule_name: str | None = None

    @classmethod
    def name(cls) -> str:
        return "keep-for"

    def __post_init__(self) -> None:
        if self.duration <= ty.timedelta():
            raise YaesmValueError("duration must be greater than zero")

    @staticmethod
    def config_schema() -> vlp.Schema:
        def duration(value: object) -> ty.timedelta:
            if isinstance(value, str) and len(value) > 1:
                amount, unit = value[:-1], value[-1].lower()
                units = {
                    "m": "minutes",
                    "h": "hours",
                    "d": "days",
                    "w": "weeks",
                    "y": "days",
                }
                if amount.isdecimal() and unit in units:
                    number = int(amount) * (365 if unit == "y" else 1)
                    try:
                        value = ty.timedelta(**{units[unit]: number})
                    except OverflowError:
                        value = None
            if not isinstance(value, ty.timedelta) or value <= ty.timedelta():
                raise vlp.Invalid("duration must be a positive duration")
            return value

        mapping = vlp.Schema({vlp.Required("duration"): duration})
        return vlp.Schema(
            lambda value: mapping(
                {"duration": value} if isinstance(value, str | ty.timedelta) else value
            )
        )

    def retain(
        self, artifacts: ty.Sequence[BackupArtifact], now: ty.datetime
    ) -> list[BackupArtifact]:
        """Return matching artifacts created within the duration."""
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        if now.utcoffset() is None:
            raise YaesmValueError("now has an invalid timezone")
        cutoff = now.astimezone(timezone.utc) - self.duration
        matching = (
            artifact
            for artifact in artifacts
            if artifact.operation.instant >= cutoff
            and (
                self.schedule_name is None or artifact.operation.schedule_name == self.schedule_name
            )
        )
        return sorted(matching, key=lambda artifact: artifact.operation.instant, reverse=True)
