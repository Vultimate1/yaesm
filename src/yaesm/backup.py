"""Backup domain objects."""

from __future__ import annotations

import dataclasses
import re

import yaesm.ty as ty
from yaesm.errors import YaesmError
from yaesm.representation import Representation

_RepresentationT = ty.TypeVar("_RepresentationT", bound=Representation, covariant=True)

if ty.TYPE_CHECKING:
    from yaesm.pipeline import Pipeline
    from yaesm.retention import RetentionPolicyBase
    from yaesm.schedule import Schedule


class BackupError(YaesmError):
    """Raised when a backup cannot be prepared or executed."""


@dataclasses.dataclass(frozen=True)
class BackupOperation:
    """One scheduled or manually requested execution of a backup."""

    backup_name: str
    schedule_name: str
    created_at: ty.datetime

    @classmethod
    def from_artifact_name(cls, backup_name: str, artifact_name: str) -> BackupOperation:
        """Reconstruct an operation from one of its artifact names."""
        prefix = f"yaesm-{backup_name}-"
        if not artifact_name.startswith(prefix):
            raise ValueError(f"invalid artifact name: {artifact_name!r}")

        try:
            schedule_name, timestamp = artifact_name.removeprefix(prefix).rsplit(".", 1)
            created_at = ty.datetime.strptime(timestamp, "%Y_%m_%d_%H:%M")
        except ValueError as error:
            raise ValueError(f"invalid artifact name: {artifact_name!r}") from error
        if not schedule_name:
            raise ValueError(f"invalid artifact name: {artifact_name!r}")
        return cls(backup_name, schedule_name, created_at)

    @property
    def artifact_name(self) -> str:
        """Return the canonical name for the resulting artifact."""
        return self.created_at.strftime(
            f"yaesm-{self.backup_name}-{self.schedule_name}.%Y_%m_%d_%H:%M"
        )


@dataclasses.dataclass(frozen=True)
class BackupArtifact(ty.Generic[_RepresentationT]):
    """A successfully stored representation and the operation that created it."""

    operation: BackupOperation
    representation: _RepresentationT

    @property
    def name(self) -> str:
        """Return the canonical artifact name."""
        return self.operation.artifact_name


@dataclasses.dataclass(frozen=True)
class Backup:
    """A named backup definition used to create operations."""

    name: str
    pipeline: Pipeline
    schedules: tuple[Schedule, ...]
    retention_policies: tuple[RetentionPolicyBase, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][-_:@a-z0-9]*", self.name, re.IGNORECASE):
            raise ValueError(f"invalid backup name: {self.name!r}")

    def execute(self, schedule_name: str, created_at: ty.datetime) -> BackupArtifact:
        """Execute one backup operation and apply retention."""
        from yaesm.pipeline import IncrementalBase

        operation = BackupOperation(self.name, schedule_name, created_at)
        try:
            artifacts = tuple(self.pipeline.destination.cap_list(self.name))
        except YaesmError as error:
            raise BackupError(f"backup {self.name!r} failed while listing artifacts") from error

        base = None
        if artifacts:
            newest = artifacts[0]
            base = IncrementalBase(
                None,
                newest.representation,
                newest.operation.created_at,
            )
        artifact = self.pipeline.execute(operation, base)

        if self.retention_policies:
            artifacts = (artifact, *artifacts)
            retained = [
                retained
                for policy in self.retention_policies
                for retained in policy.retain(artifacts, created_at)
            ]
            expired = tuple(item for item in artifacts if item not in retained)
            if expired:
                try:
                    self.pipeline.destination.cap_delete(expired)
                except YaesmError as error:
                    raise BackupError(
                        f"backup {self.name!r} failed while deleting expired artifacts"
                    ) from error

        return artifact
