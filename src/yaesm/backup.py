"""Backup domain objects."""

from __future__ import annotations

import dataclasses
import re

import yaesm.ty as ty
from yaesm.errors import YaesmError, YaesmValueError
from yaesm.representation import DataProperty, Representation
from yaesm.schedule import schedule_name_valid

_RepresentationT = ty.TypeVar("_RepresentationT", bound=Representation, covariant=True)

if ty.TYPE_CHECKING:
    from yaesm.driver.driverbase import DriverBase
    from yaesm.retention import RetentionPolicyBase
    from yaesm.schedule import Schedule


class BackupError(YaesmError):
    """Raised when a backup cannot be prepared or executed."""


@dataclasses.dataclass(frozen=True)
class DriverSource:
    """Live data provided by a driver."""

    driver: DriverBase


@dataclasses.dataclass(frozen=True)
class BackupSource:
    """Artifacts produced by another configured backup."""

    backup_name: str


@dataclasses.dataclass(frozen=True)
class BackupOperation:
    """One scheduled or manually requested execution of a backup."""

    backup_name: str
    schedule_name: str
    created_at: ty.datetime

    def __post_init__(self) -> None:
        if not schedule_name_valid(self.schedule_name):
            raise YaesmValueError(f"invalid schedule name: {self.schedule_name!r}")

    @classmethod
    def from_artifact_name(cls, backup_name: str, artifact_name: str) -> BackupOperation:
        """Reconstruct an operation from one of its artifact names."""
        prefix = f"yaesm-{backup_name}-"
        if not artifact_name.startswith(prefix):
            raise YaesmValueError(f"invalid artifact name: {artifact_name!r}")

        try:
            schedule_name, timestamp = artifact_name.removeprefix(prefix).rsplit(".", 1)
            created_at = ty.datetime.strptime(timestamp, "%Y_%m_%d_%H:%M")
        except ValueError as error:
            raise YaesmValueError(f"invalid artifact name: {artifact_name!r}") from error
        if not schedule_name:
            raise YaesmValueError(f"invalid artifact name: {artifact_name!r}")
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
    source: DriverSource | BackupSource
    destination: DriverBase
    drivers: tuple[DriverBase, ...] = ()
    requirements: frozenset[DataProperty] = frozenset()
    schedules: tuple[Schedule, ...] = ()
    retention_policies: tuple[RetentionPolicyBase, ...] = ()

    def __post_init__(self) -> None:
        if self.name.casefold() == "global_settings" or not re.fullmatch(
            r"[a-z][-_:@a-z0-9]*", self.name, re.IGNORECASE
        ):
            raise YaesmValueError(f"invalid backup name: {self.name!r}")

    def execute(
        self,
        schedule_name: str,
        created_at: ty.datetime,
        backups: ty.Mapping[str, Backup] | None = None,
    ) -> BackupArtifact:
        """Execute one backup operation and apply retention."""
        from yaesm.pipeline import IncrementalBase, Pipeline

        pipeline_drivers = self.drivers
        operation_created_at = created_at
        source_artifacts: tuple[BackupArtifact, ...] = ()
        if isinstance(self.source, BackupSource):
            source_backup = None if backups is None else backups.get(self.source.backup_name)
            if source_backup is None:
                raise BackupError(
                    f"backup {self.name!r} references unknown source backup "
                    f"{self.source.backup_name!r}"
                )
            try:
                source_artifacts = tuple(source_backup.destination.cap_list(source_backup.name))
            except YaesmError as error:
                raise BackupError(
                    f"backup {self.name!r} failed while listing source backup "
                    f"{source_backup.name!r} artifacts"
                ) from error
            if not source_artifacts:
                raise BackupError(
                    f"backup {self.name!r} cannot run: source backup "
                    f"{source_backup.name!r} has no artifacts"
                )
            pipeline_source = source_artifacts[0]
            pipeline_drivers = (source_backup.destination, *pipeline_drivers)
            operation_created_at = pipeline_source.operation.created_at
        else:
            pipeline_source = self.source

        operation = BackupOperation(self.name, schedule_name, operation_created_at)
        try:
            artifacts = tuple(self.destination.cap_list(self.name))
        except YaesmError as error:
            raise BackupError(f"backup {self.name!r} failed while listing artifacts") from error

        if isinstance(self.source, BackupSource):
            existing = next(
                (item for item in artifacts if item.name == operation.artifact_name),
                None,
            )
            if existing is not None:
                return existing

        base = None
        if artifacts:
            newest = artifacts[0]
            source_base = next(
                (
                    item.representation
                    for item in source_artifacts
                    if item.operation.created_at == newest.operation.created_at
                ),
                None,
            )
            base = IncrementalBase(
                source_base,
                newest.representation,
                newest.operation.created_at,
            )
        artifact = Pipeline(
            pipeline_source,
            self.destination,
            pipeline_drivers,
            self.requirements,
        ).execute(operation, base)

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
                    self.destination.cap_delete(expired)
                except YaesmError as error:
                    raise BackupError(
                        f"backup {self.name!r} failed while deleting expired artifacts"
                    ) from error

        return artifact
