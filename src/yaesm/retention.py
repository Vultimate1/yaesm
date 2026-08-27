"""Backup retention policies."""

import abc
import dataclasses

import yaesm.ty as ty
from yaesm.backup import BackupArtifact


class RetentionPolicyBase(abc.ABC):
    """Base class for policies that select artifacts to retain."""

    @abc.abstractmethod
    def retain(
        self, artifacts: ty.Sequence[BackupArtifact], now: ty.datetime
    ) -> list[BackupArtifact]:
        """Return the artifacts protected by this policy."""


@dataclasses.dataclass(frozen=True)
class KeepLast(RetentionPolicyBase):
    """Retain the newest artifacts, optionally from one schedule."""

    count: int
    schedule_name: str | None = None

    def __post_init__(self) -> None:
        if self.count < 1:
            raise ValueError("count must be at least 1")

    def retain(
        self, artifacts: ty.Sequence[BackupArtifact], now: ty.datetime
    ) -> list[BackupArtifact]:
        """Return the newest matching artifacts."""
        matching = (
            artifact
            for artifact in artifacts
            if self.schedule_name is None or artifact.operation.schedule_name == self.schedule_name
        )
        return sorted(matching, key=lambda artifact: artifact.operation.created_at, reverse=True)[
            : self.count
        ]
