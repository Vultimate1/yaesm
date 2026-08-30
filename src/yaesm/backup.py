"""Backup domain objects."""

from __future__ import annotations

import dataclasses
import logging
from datetime import timedelta, timezone

import yaesm.ty as ty
from yaesm.errors import YaesmError, YaesmValueError
from yaesm.names import name_valid
from yaesm.representation import Representation
from yaesm.schedule import schedule_name_valid

_RepresentationT = ty.TypeVar("_RepresentationT", bound=Representation, covariant=True)
logger = logging.getLogger(__name__)

if ty.TYPE_CHECKING:
    from yaesm.driver.driverbase import DriverBase
    from yaesm.retention import RetentionPolicyBase
    from yaesm.schedule import Schedule


class BackupError(YaesmError):
    """Raised when a backup cannot be prepared or executed."""


def backup_name_valid(name: object) -> bool:
    """Return whether a name is safe to use in an artifact name."""
    return name_valid(name, reserved=("global_settings",))


@dataclasses.dataclass(frozen=True)
class BackupSource:
    """Artifacts produced by another configured backup."""

    backup_name: str

    def __post_init__(self) -> None:
        if not backup_name_valid(self.backup_name):
            raise YaesmValueError(f"invalid source backup name: {self.backup_name!r}")


@dataclasses.dataclass(frozen=True)
class BackupOperation:
    """One scheduled or manually requested execution of a backup."""

    backup_name: str
    schedule_name: str
    created_at: ty.datetime
    source_artifact_id: str | None = None
    previous_backup_names: tuple[str, ...] = dataclasses.field(
        default=(), repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not backup_name_valid(self.backup_name):
            raise YaesmValueError(f"invalid backup name: {self.backup_name!r}")
        if not schedule_name_valid(self.schedule_name):
            raise YaesmValueError(f"invalid schedule name: {self.schedule_name!r}")
        if not isinstance(self.created_at, ty.datetime):
            raise YaesmValueError(f"invalid created_at: {self.created_at!r}")
        if self.created_at.tzinfo is None:
            object.__setattr__(
                self,
                "created_at",
                self.created_at.replace(tzinfo=timezone.utc),
            )
        offset = self.created_at.utcoffset()
        if offset is None:
            raise YaesmValueError(f"invalid created_at timezone: {self.created_at.tzinfo!r}")
        if offset % timedelta(minutes=1):
            raise YaesmValueError("created_at UTC offset must use whole minutes")
        object.__setattr__(
            self,
            "created_at",
            self.created_at.replace(
                second=0,
                microsecond=0,
                tzinfo=timezone(offset),
            ),
        )
        if self.source_artifact_id is not None and (
            not isinstance(self.source_artifact_id, str) or not self.source_artifact_id
        ):
            raise YaesmValueError(f"invalid source artifact ID: {self.source_artifact_id!r}")
        seen = {self.backup_name}
        for name in self.previous_backup_names:
            if not backup_name_valid(name):
                raise YaesmValueError(f"invalid previous backup name: {name!r}")
            if name in seen:
                raise YaesmValueError(f"duplicate backup name: {name!r}")
            seen.add(name)

    @classmethod
    def from_artifact_name(cls, backup_name: str, artifact_name: str) -> BackupOperation:
        """Reconstruct an operation from one of its artifact names."""
        prefix = f"yaesm-{backup_name}-"
        if not artifact_name.startswith(prefix):
            raise YaesmValueError(f"invalid artifact name: {artifact_name!r}")

        try:
            schedule_name, timestamp, offset = artifact_name.removeprefix(prefix).rsplit(".", 2)
            if (
                len(offset) != 5
                or offset[0] not in "pm"
                or not offset[1:].isascii()
                or not offset[1:].isdecimal()
                or offset == "m0000"
            ):
                raise ValueError
            sign = "+" if offset[0] == "p" else "-"
            created_at = ty.datetime.strptime(
                timestamp + sign + offset[1:],
                "%Y_%m_%d_%H:%M%z",
            )
        except ValueError as error:
            raise YaesmValueError(f"invalid artifact name: {artifact_name!r}") from error
        if not schedule_name:
            raise YaesmValueError(f"invalid artifact name: {artifact_name!r}")
        return cls(backup_name, schedule_name, created_at)

    @property
    def artifact_name(self) -> str:
        """Return the canonical name for the resulting artifact."""
        offset = self.created_at.strftime("%z")
        encoded_offset = ("p" if offset[0] == "+" else "m") + offset[1:]
        timestamp = self.created_at.strftime("%Y_%m_%d_%H:%M")
        return f"yaesm-{self.backup_name}-{self.schedule_name}.{timestamp}.{encoded_offset}"

    @property
    def instant(self) -> ty.datetime:
        """Return the creation time normalized to UTC."""
        return self.created_at.astimezone(timezone.utc)


@dataclasses.dataclass(frozen=True)
class BackupArtifact(ty.Generic[_RepresentationT]):
    """A stored representation with its logical operation and original name."""

    operation: BackupOperation
    representation: _RepresentationT
    stored_name: str = dataclasses.field(default="", kw_only=True)

    def __post_init__(self) -> None:
        if not self.stored_name:
            object.__setattr__(self, "stored_name", self.name)

    @property
    def name(self) -> str:
        """Return the canonical artifact name."""
        return self.operation.artifact_name


@dataclasses.dataclass(frozen=True)
class Backup:
    """A named backup definition whose history belongs to its destination."""

    name: str
    source: DriverBase | BackupSource
    destination: DriverBase
    transforms: tuple[DriverBase, ...] = ()
    schedules: tuple[Schedule, ...] = ()
    retention_policies: tuple[RetentionPolicyBase, ...] = ()
    previous_names: tuple[str, ...] = ()
    skip_unchanged: bool = False

    def __post_init__(self) -> None:
        from yaesm.driver.driverbase import DriverBase

        if not backup_name_valid(self.name):
            raise YaesmValueError(f"invalid backup name: {self.name!r}")
        if not isinstance(self.skip_unchanged, bool):
            raise YaesmValueError("skip_unchanged must be a boolean")
        if (
            self.skip_unchanged
            and not isinstance(self.source, BackupSource)
            and "unchanged" not in self.destination.capabilities()
        ):
            raise YaesmValueError(
                f"destination driver {self.destination.name()} does not support skip_unchanged"
            )

        configured_drivers = (self.destination, *self.transforms)
        if not isinstance(self.source, BackupSource):
            configured_drivers = (self.source, *configured_drivers)
        ssh = {
            driver.ssh
            for driver in configured_drivers
            if isinstance(driver, DriverBase) and driver.ssh
        }
        if len(ssh) > 1:
            raise YaesmValueError(f"backup {self.name!r} uses more than one SSH configuration")

        seen = {self.name}
        for name in self.previous_names:
            if not backup_name_valid(name):
                raise YaesmValueError(f"invalid previous backup name: {name!r}")
            if name in seen:
                raise YaesmValueError(f"duplicate backup name: {name!r}")
            seen.add(name)

        schedule_names: dict[str, str] = {}
        for schedule in self.schedules:
            for name in schedule.names:
                if owner := schedule_names.get(name):
                    raise YaesmValueError(
                        f"schedule name {name!r} is used by both {owner!r} and {schedule.name!r}"
                    )
                schedule_names[name] = schedule.name

    @property
    def names(self) -> tuple[str, ...]:
        """Return the current and previous backup names."""
        return (self.name, *self.previous_names)

    def artifacts(self) -> tuple[BackupArtifact[Representation], ...]:
        """Return artifacts from this destination under current and previous names."""
        schedule_names = {
            name: schedule.name for schedule in self.schedules for name in schedule.names
        }
        artifacts = []
        seen = set()
        for backup_name in self.names:
            for artifact in self.destination.cap_list(backup_name):
                operation = dataclasses.replace(
                    artifact.operation,
                    backup_name=self.name,
                    schedule_name=schedule_names.get(
                        artifact.operation.schedule_name,
                        artifact.operation.schedule_name,
                    ),
                )
                normalized = dataclasses.replace(artifact, operation=operation)
                if normalized.name in seen:
                    raise BackupError(
                        f"backup {self.name!r} has multiple stored artifacts that resolve to "
                        f"{normalized.name!r}"
                    )
                seen.add(normalized.name)
                artifacts.append(normalized)
        return tuple(
            sorted(artifacts, key=lambda artifact: artifact.operation.instant, reverse=True)
        )

    def execute(
        self,
        schedule_name: str,
        created_at: ty.datetime,
        backups: ty.Mapping[str, Backup] | None = None,
    ) -> BackupArtifact:
        """Execute one backup operation and apply retention."""
        from yaesm.pipeline import IncrementalBase, Pipeline

        operation_created_at = created_at
        source_artifact_id = None
        source_artifact = None
        source_artifacts: tuple[BackupArtifact, ...] = ()
        if isinstance(self.source, BackupSource):
            source_backup = None if backups is None else backups.get(self.source.backup_name)
            if source_backup is None:
                raise BackupError(
                    f"backup {self.name!r} references unknown source backup "
                    f"{self.source.backup_name!r}"
                )
            try:
                source_artifacts = source_backup.artifacts()
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
            source_driver = source_backup.destination
            source_artifact = source_artifacts[0]
            operation_created_at = source_artifact.operation.created_at
            source_artifact_id = source_driver.artifact_id(source_artifact)
        else:
            source_driver = self.source

        operation = BackupOperation(
            self.name,
            schedule_name,
            operation_created_at,
            source_artifact_id=source_artifact_id,
            previous_backup_names=self.previous_names,
        )
        pipeline = Pipeline(
            source_driver,
            self.destination,
            self.transforms,
            source_artifact=source_artifact,
        )
        try:
            artifacts = self.artifacts()
        except YaesmError as error:
            raise BackupError(f"backup {self.name!r} failed while listing artifacts") from error

        existing = next(
            (item for item in artifacts if item.name == operation.artifact_name),
            None,
        )
        if existing is not None:
            if isinstance(self.source, BackupSource):
                return existing
            raise BackupError(
                f"backup {self.name!r} already has artifact {operation.artifact_name!r}"
            )

        if self.skip_unchanged and artifacts:
            if source_artifact is not None:
                unchanged = source_artifact_id == self.destination.source_artifact_id(artifacts[0])
            else:
                temporary = None
                try:
                    source_state = source_driver.cap_source()
                    if "snapshot" in source_driver.capabilities():
                        source_state = temporary = source_driver.cap_snapshot(source_state)
                    unchanged = self.destination.cap_unchanged(
                        source_state,
                        artifacts[0],
                    )
                except YaesmError as error:
                    logger.warning(
                        "backup %r: could not determine whether source changed; continuing: %s",
                        self.name,
                        error.format(),
                    )
                    unchanged = False
                finally:
                    if temporary is not None:
                        try:
                            source_driver.cap_cleanup(temporary)
                        except YaesmError as error:
                            logger.warning(
                                "backup %r: could not clean up change-detection snapshot; "
                                "continuing: %s",
                                self.name,
                                error.format(),
                            )
                            unchanged = False
            if unchanged:
                logger.info("backup %r: source unchanged; skipped", self.name)
                return artifacts[0]

        base = None
        if artifacts:
            newest = artifacts[0]
            if source_artifact is not None:
                source_base_id = self.destination.source_artifact_id(newest)
                source_base = next(
                    (
                        item.representation
                        for item in source_artifacts
                        if source_driver.artifact_id(item) == source_base_id
                    ),
                    None,
                )
                base = IncrementalBase(
                    source_base,
                    newest.representation,
                    newest.operation.created_at,
                )
            else:
                base = IncrementalBase(
                    None,
                    newest.representation,
                    newest.operation.created_at,
                )
        artifact = pipeline.execute(operation, base)

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
