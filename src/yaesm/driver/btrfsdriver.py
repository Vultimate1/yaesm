"""Btrfs driver and representations."""

import dataclasses
import uuid
from pathlib import Path

import voluptuous as vlp

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm.check import Check, CheckRole
from yaesm.driver.driverbase import DriverBase, DriverError, GlobalSettings, capability
from yaesm.errors import YaesmValueError
from yaesm.representation import CommandStream, PathTree
from yaesm.ssh import SSHTarget, command_for_target, same_endpoint


class BtrfsDriverError(DriverError):
    """Raised when a Btrfs capability cannot be performed."""


@dataclasses.dataclass(frozen=True)
class BtrfsSubvolume(PathTree):
    """A Btrfs subvolume available at a local or remote path."""

    path: ty.Path
    target: SSHTarget | None = None


@dataclasses.dataclass(frozen=True)
class BtrfsSnapshot(BtrfsSubvolume):
    """A read-only Btrfs snapshot."""

    uuid: str | None = None
    source_uuid: str | None = None


@dataclasses.dataclass(frozen=True)
class BtrfsStream(CommandStream):
    """A Btrfs send stream."""

    subvolume_name: str = dataclasses.field(kw_only=True)


class BtrfsDriver(DriverBase):
    """Provide Btrfs backup capabilities for a configured location."""

    def __init__(
        self,
        location: ty.Path,
        target: SSHTarget | None = None,
        bootstrap_refresh_days: int = 21,
        *,
        global_settings: GlobalSettings | None = None,
    ) -> None:
        super().__init__(global_settings)
        if bootstrap_refresh_days < 0:
            raise YaesmValueError(
                f"bootstrap_refresh_days must be at least 0, got {bootstrap_refresh_days}"
            )
        self.location = Path(location)
        self.target = target
        self.bootstrap_refresh_days = bootstrap_refresh_days or None

    @classmethod
    def name(cls) -> str:
        return "btrfs"

    @staticmethod
    def config_schema() -> vlp.Schema:
        def absolute_path(value: object) -> ty.Path:
            if not isinstance(value, str | Path):
                raise vlp.Invalid("location must be a path")
            path = Path(value)
            if not path.is_absolute():
                raise vlp.Invalid("location must be an absolute path")
            return path

        def ssh_target(value: object) -> SSHTarget:
            if not isinstance(value, SSHTarget):
                raise vlp.Invalid("target must be an SSHTarget")
            return value

        def refresh_days(value: object) -> int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise vlp.Invalid("bootstrap_refresh_days must be an integer")
            if value < 0:
                raise vlp.Invalid("bootstrap_refresh_days must be at least 0")
            return value

        return vlp.Schema(
            {
                vlp.Required("location"): absolute_path,
                vlp.Optional("target"): ssh_target,
                vlp.Optional("bootstrap_refresh_days", default=21): refresh_days,
            }
        )

    def _checks(self, role: CheckRole) -> tuple[Check, ...]:
        match role:
            case CheckRole.SOURCE:
                requirements = (
                    ("directory exists", ("test", "-d", self.location)),
                    (
                        "directory is a Btrfs subvolume",
                        ("btrfs", "subvolume", "show", self.location),
                    ),
                    ("directory is readable", ("test", "-r", self.location)),
                    ("directory is writable", ("test", "-w", self.location)),
                    ("directory is searchable", ("test", "-x", self.location)),
                )
            case CheckRole.ARTIFACT_SOURCE:
                requirements = (
                    ("directory exists", ("test", "-d", self.location)),
                    (
                        "directory is on a Btrfs filesystem",
                        ("btrfs", "filesystem", "usage", self.location),
                    ),
                    ("directory is readable", ("test", "-r", self.location)),
                    ("directory is searchable", ("test", "-x", self.location)),
                )
            case CheckRole.DESTINATION:
                requirements = (
                    ("directory exists", ("test", "-d", self.location)),
                    (
                        "directory is on a Btrfs filesystem",
                        ("btrfs", "filesystem", "usage", self.location),
                    ),
                    ("directory is readable", ("test", "-r", self.location)),
                    ("directory is writable", ("test", "-w", self.location)),
                    ("directory is searchable", ("test", "-x", self.location)),
                )
            case CheckRole.TRANSFORM:
                return ()
        return tuple(
            self._command_check(
                f"{description}: {self.location}",
                command,
            )
            for description, command in requirements
        )

    def _check_target(self) -> SSHTarget | None:
        return self.target

    def cap_source(self) -> BtrfsSubvolume:
        return BtrfsSubvolume(self.location, self.target)

    def cap_snapshot(self, source: BtrfsSubvolume) -> BtrfsSnapshot:
        snapshot = BtrfsSnapshot(
            source.path / f".yaesm-btrfs-staging-{uuid.uuid4().hex}",
            source.target,
        )
        self.runner.run(
            command_for_target(
                source.target,
                ("btrfs", "subvolume", "snapshot", "-r", source.path, snapshot.path),
            )
        )
        return snapshot

    @capability("store", base="source")
    def cap_store(
        self,
        source: BtrfsSubvolume,
        operation: bckp.BackupOperation,
        base: BtrfsSnapshot | None = None,
    ) -> bckp.BackupArtifact[BtrfsSnapshot]:
        destination = BtrfsSnapshot(self.location / operation.artifact_name, self.target)
        artifact = bckp.BackupArtifact(operation, destination)

        if same_endpoint(source.target, self.target):
            result = self.runner.run(
                command_for_target(
                    self.target,
                    (
                        "btrfs",
                        "subvolume",
                        "snapshot",
                        "-r",
                        source.path,
                        destination.path,
                    ),
                ),
                check=False,
            )
            if result.returncode == 0:
                return artifact

        if base is None:
            base = self._bootstrap(source, operation.backup_name)
        snapshot = self.cap_snapshot(source)
        try:
            return self.cap_import(self.cap_export(snapshot, base), operation)
        finally:
            self.cap_cleanup(snapshot)

    def cap_export(self, source: BtrfsSnapshot, base: BtrfsSnapshot | None = None) -> BtrfsStream:
        if base is not None and not same_endpoint(source.target, base.target):
            raise BtrfsDriverError("Btrfs export and base use different SSH endpoints")

        command: list[str | ty.Path] = ["btrfs", "send", "--compressed-data"]
        if base is not None:
            command.extend(("-p", base.path))
        command.append(source.path)
        return BtrfsStream(
            (command_for_target(source.target, command),),
            subvolume_name=source.path.name,
        )

    def cap_import(
        self,
        source: BtrfsStream,
        operation: bckp.BackupOperation,
        base: BtrfsSnapshot | None = None,
    ) -> bckp.BackupArtifact[BtrfsSnapshot]:
        if base is not None and not same_endpoint(base.target, self.target):
            raise BtrfsDriverError("Btrfs import and base use different SSH endpoints")

        received = self.location / source.subvolume_name
        destination = self.location / operation.artifact_name
        try:
            self.runner.pipeline(
                (
                    *source.commands,
                    command_for_target(
                        self.target,
                        ("btrfs", "receive", self.location),
                    ),
                )
            )
            self.runner.run(
                command_for_target(
                    self.target,
                    ("mv", "-T", "--", received, destination),
                )
            )
        except BaseException:
            self._delete((BtrfsSnapshot(received, self.target),), check=False)
            raise

        return bckp.BackupArtifact(
            operation,
            BtrfsSnapshot(destination, self.target),
        )

    def cap_list(self, backup_name: str) -> tuple[bckp.BackupArtifact[BtrfsSnapshot], ...]:
        result = self.runner.run(
            command_for_target(
                self.target,
                (
                    "btrfs",
                    "subvolume",
                    "list",
                    "-u",
                    "-q",
                    "-R",
                    "-o",
                    self.location,
                ),
            ),
            capture_output=True,
        )
        artifacts = []
        for line in (result.stdout or "").splitlines():
            snapshot = self._parse_snapshot(line)
            if snapshot is None:
                continue
            try:
                operation = bckp.BackupOperation.from_artifact_name(
                    backup_name,
                    snapshot.path.name,
                )
            except YaesmValueError:
                continue
            operation = dataclasses.replace(
                operation,
                source_artifact_id=snapshot.source_uuid,
            )
            artifacts.append(bckp.BackupArtifact(operation, snapshot))
        return tuple(
            sorted(artifacts, key=lambda artifact: artifact.operation.created_at, reverse=True)
        )

    def format_locator(self, artifact: bckp.BackupArtifact[BtrfsSnapshot]) -> str:
        snapshot = artifact.representation
        return (
            str(snapshot.path)
            if snapshot.target is None
            else snapshot.target.format_location(snapshot.path)
        )

    def artifact_id(self, artifact: bckp.BackupArtifact) -> str:
        """Return the snapshot UUID when Btrfs reported one."""
        snapshot = ty.cast(BtrfsSnapshot, artifact.representation)
        return snapshot.uuid or super().artifact_id(artifact)

    def cap_delete(
        self,
        artifacts: ty.Sequence[bckp.BackupArtifact[BtrfsSnapshot]],
    ) -> None:
        snapshots = tuple(artifact.representation for artifact in artifacts)
        if any(not same_endpoint(snapshot.target, self.target) for snapshot in snapshots):
            raise BtrfsDriverError("Btrfs artifact uses a different SSH endpoint")
        self._delete(snapshots)

    def cap_cleanup(self, representation: BtrfsSnapshot) -> None:
        self._delete((representation,))

    def _parse_snapshot(self, line: str) -> BtrfsSnapshot | None:
        fields, separator, path = line.partition(" path ")
        values = fields.split()
        if not separator:
            return None
        try:
            uuid_ = values[values.index("uuid") + 1]
            parent_uuid = values[values.index("parent_uuid") + 1]
            received_uuid = values[values.index("received_uuid") + 1]
        except (IndexError, ValueError):
            return None
        source_uuid = received_uuid if received_uuid != "-" else parent_uuid
        return BtrfsSnapshot(
            self.location / Path(path).name,
            self.target,
            uuid_,
            None if source_uuid == "-" else source_uuid,
        )

    def _bootstrap(self, source: BtrfsSubvolume, backup_name: str) -> BtrfsSnapshot:
        name = f".yaesm-btrfs-bootstrap-{backup_name}"
        source_bootstrap = BtrfsSnapshot(source.path / name, source.target)
        destination_bootstrap = BtrfsSnapshot(self.location / name, self.target)
        source_exists = self._exists(source_bootstrap)
        destination_exists = self._exists(destination_bootstrap)

        if source_exists and self._stale(source_bootstrap):
            self._delete((source_bootstrap,))
            if destination_exists:
                self._delete((destination_bootstrap,))
            source_exists = destination_exists = False

        if not source_exists:
            if destination_exists:
                self._delete((destination_bootstrap,))
            self.runner.run(
                command_for_target(
                    source.target,
                    (
                        "btrfs",
                        "subvolume",
                        "snapshot",
                        "-r",
                        source.path,
                        source_bootstrap.path,
                    ),
                )
            )

        if not source_exists or not destination_exists:
            stream = self.cap_export(source_bootstrap)
            try:
                self.runner.pipeline(
                    (
                        *stream.commands,
                        command_for_target(
                            self.target,
                            ("btrfs", "receive", self.location),
                        ),
                    )
                )
            except BaseException:
                self._delete((destination_bootstrap,), check=False)
                raise

        return source_bootstrap

    def _stale(self, snapshot: BtrfsSnapshot) -> bool:
        if self.bootstrap_refresh_days is None:
            return False
        result = self.runner.run(
            command_for_target(
                snapshot.target,
                (
                    "find",
                    snapshot.path,
                    "-prune",
                    "-mtime",
                    f"+{self.bootstrap_refresh_days - 1}",
                    "-print",
                ),
            ),
            capture_output=True,
        )
        return bool(result.stdout)

    def _exists(self, snapshot: BtrfsSnapshot) -> bool:
        return (
            self.runner.run(
                command_for_target(
                    snapshot.target,
                    ("btrfs", "subvolume", "show", snapshot.path),
                ),
                check=False,
            ).returncode
            == 0
        )

    def _delete(
        self,
        snapshots: ty.Sequence[BtrfsSnapshot],
        *,
        check: bool = True,
    ) -> None:
        if not snapshots:
            return
        self.runner.run(
            command_for_target(
                snapshots[0].target,
                ("btrfs", "subvolume", "delete", *(snapshot.path for snapshot in snapshots)),
            ),
            check=check,
        )
