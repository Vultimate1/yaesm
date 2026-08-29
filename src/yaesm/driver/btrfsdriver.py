"""Btrfs driver and representations."""

import dataclasses
from pathlib import Path
from uuid import UUID, uuid4

import voluptuous as vlp

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm.check import Check, CheckRole
from yaesm.command import CommandStage
from yaesm.driver.driverbase import DriverBase, DriverError, GlobalSettings, capability
from yaesm.errors import YaesmValueError
from yaesm.representation import CommandStream, DataProperty, PathTree, Representation
from yaesm.ssh import SSHTarget, command_for_ssh, same_endpoint


class BtrfsDriverError(DriverError):
    """Raised when a Btrfs capability cannot be performed."""


@dataclasses.dataclass(frozen=True)
class BtrfsSubvolume(PathTree):
    """A Btrfs subvolume available at a local or remote path."""

    path: ty.Path
    ssh: SSHTarget | None = None


@dataclasses.dataclass(frozen=True)
class BtrfsSnapshot(BtrfsSubvolume):
    """A read-only Btrfs snapshot."""

    uuid: UUID = dataclasses.field(kw_only=True)
    source_uuid: UUID | None = dataclasses.field(default=None, kw_only=True)


@dataclasses.dataclass(frozen=True)
class BtrfsStream(CommandStream):
    """A Btrfs send stream."""

    suffix = ".btrfs"
    subvolume_name: str = dataclasses.field(kw_only=True)
    base_uuid: UUID | None = dataclasses.field(default=None, kw_only=True)


class BtrfsDriver(DriverBase):
    """Provide Btrfs backup capabilities for a configured location."""

    def __init__(
        self,
        location: ty.Path,
        ssh: SSHTarget | None = None,
        *,
        global_settings: GlobalSettings | None = None,
    ) -> None:
        super().__init__(global_settings, ssh=ssh)
        self.location = Path(location)

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

        mapping = vlp.Schema({vlp.Required("location"): absolute_path})
        return vlp.Schema(
            lambda value: mapping({"location": value} if isinstance(value, str | Path) else value)
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

    def _base_compatible(
        self,
        capability: str,
        source: Representation,
        source_base: Representation | None,
        destination_base: Representation | None,
    ) -> bool:
        if (
            not isinstance(source_base, BtrfsSnapshot)
            or not isinstance(destination_base, BtrfsSnapshot)
            or destination_base.source_uuid != source_base.uuid
        ):
            return False
        if capability in {"store", "export"}:
            return (
                isinstance(source, BtrfsSnapshot)
                and same_endpoint(source.ssh, source_base.ssh)
                and (capability == "export" or same_endpoint(destination_base.ssh, self.ssh))
            )
        return (
            capability == "import"
            and isinstance(source, BtrfsStream)
            and source.base_uuid == source_base.uuid
            and same_endpoint(destination_base.ssh, self.ssh)
        )

    def cap_source(self) -> BtrfsSubvolume:
        return BtrfsSubvolume(self.location, self.ssh)

    def cap_snapshot(self, source: BtrfsSubvolume) -> BtrfsSnapshot:
        return self._snapshot(
            source,
            BtrfsSubvolume(
                source.path / f".yaesm-btrfs-staging-{uuid4().hex}",
                source.ssh,
            ),
        )

    def _snapshot(
        self,
        source: BtrfsSubvolume,
        snapshot: BtrfsSubvolume,
    ) -> BtrfsSnapshot:
        self.runner.run(
            command_for_ssh(
                source.ssh,
                ("btrfs", "subvolume", "snapshot", "-r", source.path, snapshot.path),
            )
        )
        try:
            return self._read_snapshot(snapshot)
        except BaseException:
            self._delete((snapshot,), check=False)
            raise

    @capability("store", adds=(DataProperty.SNAPSHOT,), base="source")
    def cap_store(
        self,
        source: BtrfsSubvolume,
        operation: bckp.BackupOperation,
        base: BtrfsSnapshot | None = None,
    ) -> bckp.BackupArtifact[BtrfsSnapshot]:
        destination = BtrfsSubvolume(self.location / operation.artifact_name, self.ssh)

        if same_endpoint(source.ssh, self.ssh):
            result = self.runner.run(
                command_for_ssh(
                    self.ssh,
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
                try:
                    return bckp.BackupArtifact(operation, self._read_snapshot(destination))
                except BaseException:
                    self._delete((destination,), check=False)
                    raise

        if isinstance(source, BtrfsSnapshot):
            return self.cap_import(self.cap_export(source, base), operation)

        rolling = base is None
        if rolling:
            base = self._rolling_base(source, operation)
            snapshot = self._snapshot(
                source,
                BtrfsSubvolume(
                    source.path / self._pending_name(operation.backup_name),
                    source.ssh,
                ),
            )
        else:
            snapshot = self.cap_snapshot(source)
        promoted = False
        try:
            artifact = self.cap_import(self.cap_export(snapshot, base), operation)
            if rolling:
                self._promote_base(snapshot, source, operation)
                promoted = True
            return artifact
        finally:
            if not promoted:
                self.cap_cleanup(snapshot)

    def cap_export(self, source: BtrfsSnapshot, base: BtrfsSnapshot | None = None) -> BtrfsStream:
        if base is not None and not same_endpoint(source.ssh, base.ssh):
            raise BtrfsDriverError("Btrfs export and base use different SSH endpoints")

        command: list[str | ty.Path] = ["btrfs", "send", "--compressed-data"]
        if base is not None:
            command.extend(("-p", base.path))
        command.append(source.path)
        return BtrfsStream(
            (CommandStage(command, source.ssh),),
            subvolume_name=source.path.name,
            base_uuid=None if base is None else base.uuid,
            suffixes=(BtrfsStream.suffix,),
        )

    def cap_import(
        self,
        source: BtrfsStream,
        operation: bckp.BackupOperation,
        base: BtrfsSnapshot | None = None,
    ) -> bckp.BackupArtifact[BtrfsSnapshot]:
        if base is not None and not same_endpoint(base.ssh, self.ssh):
            raise BtrfsDriverError("Btrfs import and base use different SSH endpoints")

        received = BtrfsSubvolume(self.location / source.subvolume_name, self.ssh)
        destination = BtrfsSubvolume(self.location / operation.artifact_name, self.ssh)
        stored = received
        try:
            self.runner.pipeline(
                (
                    *source.stages,
                    CommandStage(("btrfs", "receive", self.location), self.ssh),
                )
            )
            self.runner.run(
                command_for_ssh(
                    self.ssh,
                    ("mv", "-T", "--", received.path, destination.path),
                )
            )
            stored = destination
            snapshot = self._read_snapshot(destination)
        except BaseException:
            self._delete((stored,), check=False)
            raise

        return bckp.BackupArtifact(operation, snapshot)

    def cap_list(self, backup_name: str) -> tuple[bckp.BackupArtifact[BtrfsSnapshot], ...]:
        result = self.runner.run(
            command_for_ssh(
                self.ssh,
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
                source_artifact_id=(
                    None if snapshot.source_uuid is None else str(snapshot.source_uuid)
                ),
            )
            artifacts.append(bckp.BackupArtifact(operation, snapshot))
        return tuple(
            sorted(artifacts, key=lambda artifact: artifact.operation.created_at, reverse=True)
        )

    def format_locator(self, artifact: bckp.BackupArtifact[BtrfsSnapshot]) -> str:
        snapshot = artifact.representation
        return (
            str(snapshot.path)
            if snapshot.ssh is None
            else snapshot.ssh.format_location(snapshot.path)
        )

    def artifact_id(self, artifact: bckp.BackupArtifact) -> str:
        """Return the snapshot UUID."""
        snapshot = ty.cast(BtrfsSnapshot, artifact.representation)
        return str(snapshot.uuid)

    def cap_delete(
        self,
        artifacts: ty.Sequence[bckp.BackupArtifact[BtrfsSnapshot]],
    ) -> None:
        snapshots = tuple(artifact.representation for artifact in artifacts)
        if any(not same_endpoint(snapshot.ssh, self.ssh) for snapshot in snapshots):
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
        try:
            snapshot_uuid = UUID(uuid_)
            source_uuid_value = received_uuid if received_uuid != "-" else parent_uuid
            source_uuid = None if source_uuid_value == "-" else UUID(source_uuid_value)
        except ValueError:
            return None
        return BtrfsSnapshot(
            self.location / Path(path).name,
            self.ssh,
            uuid=snapshot_uuid,
            source_uuid=source_uuid,
        )

    @staticmethod
    def _base_name(backup_name: str) -> str:
        return f".yaesm-btrfs-base-{backup_name}"

    @staticmethod
    def _pending_name(backup_name: str) -> str:
        return f".yaesm-btrfs-pending-{backup_name}"

    def _rolling_base(
        self,
        source: BtrfsSubvolume,
        operation: bckp.BackupOperation,
    ) -> BtrfsSnapshot | None:
        names = (operation.backup_name, *operation.previous_backup_names)
        pending = self._named_snapshots(source, names, self._pending_name)
        bases = self._named_snapshots(source, names, self._base_name)
        candidates = (*pending, *bases)
        if not candidates:
            return None

        source_uuids = {
            artifact.representation.source_uuid
            for name in names
            for artifact in self.cap_list(name)
        }
        if recovered := next(
            (snapshot for snapshot in pending if snapshot.uuid in source_uuids),
            None,
        ):
            self._promote_base(recovered, source, operation)
            return dataclasses.replace(
                recovered,
                path=source.path / self._base_name(operation.backup_name),
            )

        self._delete(pending)
        base = next((snapshot for snapshot in bases if snapshot.uuid in source_uuids), None)
        self._delete(tuple(snapshot for snapshot in bases if snapshot is not base))
        return base

    def _promote_base(
        self,
        snapshot: BtrfsSnapshot,
        source: BtrfsSubvolume,
        operation: bckp.BackupOperation,
    ) -> None:
        names = (operation.backup_name, *operation.previous_backup_names)
        self._delete(self._named_snapshots(source, names, self._base_name))
        self.runner.run(
            command_for_ssh(
                source.ssh,
                (
                    "mv",
                    "-T",
                    "--",
                    snapshot.path,
                    source.path / self._base_name(operation.backup_name),
                ),
            )
        )

    def _named_snapshots(
        self,
        source: BtrfsSubvolume,
        names: ty.Sequence[str],
        name: ty.Callable[[str], str],
    ) -> tuple[BtrfsSnapshot, ...]:
        return tuple(
            snapshot
            for backup_name in names
            if (
                snapshot := self._find_snapshot(
                    BtrfsSubvolume(source.path / name(backup_name), source.ssh)
                )
            )
            is not None
        )

    def _read_snapshot(self, snapshot: BtrfsSubvolume) -> BtrfsSnapshot:
        result = self.runner.run(
            command_for_ssh(
                snapshot.ssh,
                ("btrfs", "subvolume", "show", snapshot.path),
            ),
            capture_output=True,
        )
        return self._read_snapshot_from_output(snapshot, result.stdout)

    def _find_snapshot(self, snapshot: BtrfsSubvolume) -> BtrfsSnapshot | None:
        result = self.runner.run(
            command_for_ssh(
                snapshot.ssh,
                ("btrfs", "subvolume", "show", snapshot.path),
            ),
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return self._read_snapshot_from_output(snapshot, result.stdout)

    def _read_snapshot_from_output(
        self,
        snapshot: BtrfsSubvolume,
        output: str | None,
    ) -> BtrfsSnapshot:
        values = {}
        for line in (output or "").splitlines():
            name, separator, value = line.partition(":")
            if separator:
                values[name.strip()] = value.strip()

        uuid_value = values.get("UUID")
        if not uuid_value or uuid_value == "-":
            raise BtrfsDriverError(f"could not read Btrfs snapshot UUID: {snapshot.path}")
        source_uuid_value = values.get("Received UUID")
        if not source_uuid_value or source_uuid_value == "-":
            source_uuid_value = values.get("Parent UUID")
        try:
            snapshot_uuid = UUID(uuid_value)
            source_uuid = (
                None
                if not source_uuid_value or source_uuid_value == "-"
                else UUID(source_uuid_value)
            )
        except ValueError as error:
            raise BtrfsDriverError(
                f"could not read Btrfs snapshot UUID: {snapshot.path}"
            ) from error
        return BtrfsSnapshot(
            snapshot.path,
            snapshot.ssh,
            uuid=snapshot_uuid,
            source_uuid=source_uuid,
        )

    def _delete(
        self,
        snapshots: ty.Sequence[BtrfsSubvolume],
        *,
        check: bool = True,
    ) -> None:
        if not snapshots:
            return
        self.runner.run(
            command_for_ssh(
                snapshots[0].ssh,
                ("btrfs", "subvolume", "delete", *(snapshot.path for snapshot in snapshots)),
            ),
            check=check,
        )
