"""ZFS driver and representations."""

import dataclasses
from uuid import uuid4

import voluptuous as vlp

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm.check import Check, CheckRole
from yaesm.command import CommandError, CommandResult
from yaesm.driver.driverbase import (
    CapabilityMetadata,
    DriverBase,
    DriverError,
    GlobalSettings,
    capability,
)
from yaesm.errors import YaesmValueError
from yaesm.representation import CommandStream, DataProperty, Representation
from yaesm.ssh import SSHTarget, command_for_ssh, same_endpoint

_SOURCE_ARTIFACT_PROPERTY = "yaesm:source-artifact"


class ZFSDriverError(DriverError):
    """Raised when a ZFS capability cannot be performed."""


@dataclasses.dataclass(frozen=True)
class ZFSDataset(Representation):
    """A ZFS filesystem available locally or remotely."""

    name: str
    ssh: SSHTarget | None = None
    encrypted: bool = False


@dataclasses.dataclass(frozen=True)
class ZFSSnapshot(Representation):
    """A snapshot of a local or remote ZFS filesystem."""

    dataset: str
    snapshot: str
    ssh: SSHTarget | None = None
    encrypted: bool = False
    guid: int | None = dataclasses.field(default=None, kw_only=True, compare=False)

    @property
    def name(self) -> str:
        """Return the complete ZFS snapshot name."""
        return f"{self.dataset}@{self.snapshot}"


@dataclasses.dataclass(frozen=True)
class ZFSStream(CommandStream):
    """A ZFS send stream."""

    suffix = ".zfs"
    encrypted: bool = False
    base_guid: int | None = dataclasses.field(default=None, kw_only=True)


class ZFSDriver(DriverBase):
    """Provide ZFS backup capabilities for a configured dataset."""

    def __init__(
        self,
        dataset: str,
        ssh: SSHTarget | None = None,
        encryption: bool = False,
        *,
        global_settings: GlobalSettings | None = None,
    ) -> None:
        super().__init__(global_settings)
        if not _dataset_valid(dataset):
            raise YaesmValueError(f"invalid ZFS dataset: {dataset!r}")
        if not isinstance(encryption, bool):
            raise YaesmValueError("encryption must be a boolean")
        self.dataset = dataset
        self.ssh = ssh
        self.encryption = encryption

    @classmethod
    def name(cls) -> str:
        return "zfs"

    @staticmethod
    def config_schema() -> vlp.Schema:
        def dataset(value: object) -> str:
            if not _dataset_valid(value):
                raise vlp.Invalid("dataset must be a ZFS filesystem name")
            return ty.cast(str, value)

        def ssh(value: object) -> SSHTarget:
            if not isinstance(value, SSHTarget):
                raise vlp.Invalid("ssh must be an SSHTarget")
            return value

        def encryption(value: object) -> bool:
            if not isinstance(value, bool):
                raise vlp.Invalid("encryption must be a boolean")
            return value

        mapping = vlp.Schema(
            {
                vlp.Required("dataset"): dataset,
                vlp.Optional("ssh"): ssh,
                vlp.Optional("encryption"): encryption,
            }
        )
        return vlp.Schema(
            lambda value: mapping({"dataset": value} if isinstance(value, str) else value)
        )

    def _checks(self, role: CheckRole) -> tuple[Check, ...]:
        match role:
            case CheckRole.SOURCE | CheckRole.ARTIFACT_SOURCE:
                dataset_check = self._command_check(
                    f"source dataset exists: {self.dataset}",
                    ("zfs", "list", "-H", "-t", "filesystem", "-o", "name", self.dataset),
                )
                if not self.encryption:
                    return (dataset_check,)
                return (
                    dataset_check,
                    self._command_check(
                        f"source dataset is encrypted: {self.dataset}",
                        (
                            "zfs",
                            "get",
                            "-H",
                            "-o",
                            "value",
                            "encryption",
                            self.dataset,
                        ),
                        validate=lambda result: _encryption_failure(self.dataset, result),
                    ),
                )
            case CheckRole.DESTINATION:
                parent = self.dataset.rpartition("/")[0] or self.dataset
                return (
                    self._command_check(
                        f"destination parent dataset exists: {parent}",
                        ("zfs", "list", "-H", "-t", "filesystem", "-o", "name", parent),
                    ),
                    self._command_check(
                        f"destination dataset can be created: {self.dataset}",
                        ("zfs", "create", "-n", "-p", "-u", self.dataset),
                    ),
                )
            case CheckRole.TRANSFORM:
                return ()

    def _check_ssh(self) -> SSHTarget | None:
        return self.ssh

    def _base_compatible(
        self,
        capability: str,
        source: Representation,
        source_base: Representation | None,
        destination_base: Representation | None,
    ) -> bool:
        if not isinstance(destination_base, ZFSSnapshot):
            return False
        if capability == "store":
            if not isinstance(source, ZFSDataset) or not self._owns(destination_base):
                return False
            if same_endpoint(source.ssh, self.ssh) and source.name == self.dataset:
                return False
            source_base = ZFSSnapshot(
                source.name,
                destination_base.snapshot,
                source.ssh,
                source.encrypted,
            )
        elif capability == "export":
            if source_base is None:
                raise ZFSDriverError("ZFS source incremental base snapshot is missing")
            if (
                not isinstance(source, ZFSSnapshot)
                or not isinstance(source_base, ZFSSnapshot)
                or not same_endpoint(source.ssh, source_base.ssh)
                or source.dataset != source_base.dataset
            ):
                return False
        elif capability == "import":
            if (
                not isinstance(source, ZFSStream)
                or not isinstance(source_base, ZFSSnapshot)
                or not self._owns(destination_base)
            ):
                return False
        else:
            return False

        source_guid = self._snapshot_guid(source_base)
        if source_guid != self._snapshot_guid(destination_base):
            raise ZFSDriverError(
                "ZFS incremental base snapshots have different GUIDs: "
                f"{source_base.name} and {destination_base.name}"
            )
        if isinstance(source, ZFSStream) and source.base_guid != source_guid:
            raise ZFSDriverError("ZFS stream does not match its incremental base")
        return True

    def capability_metadata(self, name: str) -> CapabilityMetadata:
        metadata = super().capability_metadata(name)
        if self.encryption and name in {"source", "store", "export"}:
            return dataclasses.replace(
                metadata,
                adds=metadata.adds | {DataProperty.ENCRYPTED},
            )
        return metadata

    def cap_source(self) -> ZFSDataset:
        if self.encryption:
            self._require_encrypted(self.dataset, self.ssh)
        return ZFSDataset(self.dataset, self.ssh, self.encryption)

    def cap_snapshot(self, source: ZFSDataset) -> ZFSSnapshot:
        snapshot = ZFSSnapshot(
            source.name,
            f".yaesm-zfs-staging-{uuid4().hex}",
            source.ssh,
            source.encrypted,
        )
        self.runner.run(command_for_ssh(source.ssh, ("zfs", "snapshot", snapshot.name)))
        return snapshot

    @capability("store", adds=(DataProperty.SNAPSHOT,), base="destination")
    def cap_store(
        self,
        source: ZFSDataset,
        operation: bckp.BackupOperation,
        base: ZFSSnapshot | None = None,
    ) -> bckp.BackupArtifact[ZFSSnapshot]:
        encrypted = source.encrypted or self.encryption
        if self.encryption and not source.encrypted:
            self._require_encrypted(source.name, source.ssh)
        destination = ZFSSnapshot(
            self.dataset,
            operation.artifact_name,
            self.ssh,
            encrypted,
        )
        if same_endpoint(source.ssh, self.ssh) and source.name == self.dataset:
            self.runner.run(command_for_ssh(source.ssh, ("zfs", "snapshot", destination.name)))
            return self._record_artifact(bckp.BackupArtifact(operation, destination))

        if base is not None and not self._owns(base):
            raise ZFSDriverError("ZFS base does not belong to the destination dataset")

        source_snapshot = ZFSSnapshot(
            source.name,
            operation.artifact_name,
            source.ssh,
            encrypted,
        )
        source_base = (
            None
            if base is None
            else ZFSSnapshot(
                source.name,
                base.snapshot,
                source.ssh,
                encrypted,
                guid=base.guid,
            )
        )
        self.runner.run(command_for_ssh(source.ssh, ("zfs", "snapshot", source_snapshot.name)))
        try:
            artifact = self.cap_import(
                self.cap_export(source_snapshot, source_base),
                operation,
                base,
            )
        except BaseException:
            self._destroy((source_snapshot,), check=False)
            raise
        if source_base is not None:
            self._destroy((source_base,))
        return artifact

    def cap_export(
        self,
        source: ZFSSnapshot,
        base: ZFSSnapshot | None = None,
    ) -> ZFSStream:
        if base is not None and (
            not same_endpoint(source.ssh, base.ssh) or source.dataset != base.dataset
        ):
            raise ZFSDriverError("ZFS export and base use different datasets")

        encrypted = source.encrypted or self.encryption
        if self.encryption and not source.encrypted:
            self._require_encrypted(source.dataset, source.ssh)

        command = ["zfs", "send"]
        if encrypted:
            command.append("-w")
        else:
            command.append("-c")
        if base is not None:
            command.extend(("-i", base.name))
        command.append(source.name)
        return ZFSStream(
            (command_for_ssh(source.ssh, command),),
            encrypted,
            base_guid=None if base is None else base.guid,
            suffixes=(ZFSStream.suffix,),
        )

    def cap_import(
        self,
        source: ZFSStream,
        operation: bckp.BackupOperation,
        base: ZFSSnapshot | None = None,
    ) -> bckp.BackupArtifact[ZFSSnapshot]:
        if base is not None and not self._owns(base):
            raise ZFSDriverError("ZFS import base does not belong to the destination dataset")

        destination = ZFSSnapshot(
            self.dataset,
            operation.artifact_name,
            self.ssh,
            source.encrypted,
        )
        try:
            self.runner.pipeline(
                (
                    *source.commands,
                    command_for_ssh(
                        self.ssh,
                        ("zfs", "receive", "-u", "-o", "mountpoint=none", self.dataset),
                    ),
                )
            )
        except BaseException:
            self._destroy((destination,), check=False)
            raise
        return self._record_artifact(bckp.BackupArtifact(operation, destination))

    def cap_list(self, backup_name: str) -> tuple[bckp.BackupArtifact[ZFSSnapshot], ...]:
        try:
            result = self.runner.run(
                command_for_ssh(
                    self.ssh,
                    (
                        "zfs",
                        "list",
                        "-H",
                        "-t",
                        "snapshot",
                        "-o",
                        f"name,guid,{_SOURCE_ARTIFACT_PROPERTY}",
                        "-d",
                        "1",
                        self.dataset,
                    ),
                ),
                capture_output=True,
            )
        except CommandError as error:
            if "dataset does not exist" in error.stderr:
                return ()
            raise

        prefix = f"{self.dataset}@"
        artifacts = []
        for line in (result.stdout or "").splitlines():
            try:
                name, guid_value, source_artifact_id = line.split("\t", 2)
                guid = int(guid_value)
            except ValueError:
                continue
            if not name.startswith(prefix):
                continue
            snapshot_name = name.removeprefix(prefix)
            try:
                operation = bckp.BackupOperation.from_artifact_name(
                    backup_name,
                    snapshot_name,
                )
            except YaesmValueError:
                continue
            operation = dataclasses.replace(
                operation,
                source_artifact_id=(source_artifact_id if source_artifact_id != "-" else None),
            )
            artifacts.append(
                bckp.BackupArtifact(
                    operation,
                    ZFSSnapshot(self.dataset, snapshot_name, self.ssh, guid=guid),
                )
            )
        return tuple(
            sorted(artifacts, key=lambda artifact: artifact.operation.created_at, reverse=True)
        )

    def artifact_id(self, artifact: bckp.BackupArtifact) -> str:
        snapshot = ty.cast(ZFSSnapshot, artifact.representation)
        return str(snapshot.guid) if snapshot.guid is not None else super().artifact_id(artifact)

    def _record_artifact(
        self,
        artifact: bckp.BackupArtifact[ZFSSnapshot],
    ) -> bckp.BackupArtifact[ZFSSnapshot]:
        source_artifact_id = artifact.operation.source_artifact_id
        if source_artifact_id is None:
            return artifact
        try:
            self.runner.run(
                command_for_ssh(
                    self.ssh,
                    (
                        "zfs",
                        "set",
                        f"{_SOURCE_ARTIFACT_PROPERTY}={source_artifact_id}",
                        artifact.representation.name,
                    ),
                )
            )
        except BaseException:
            self._destroy((artifact.representation,), check=False)
            raise
        return artifact

    def _owns(self, snapshot: ZFSSnapshot) -> bool:
        return same_endpoint(snapshot.ssh, self.ssh) and snapshot.dataset == self.dataset

    def _snapshot_guid(self, snapshot: ZFSSnapshot) -> int:
        if snapshot.guid is not None:
            return snapshot.guid
        result = self.runner.run(
            command_for_ssh(
                snapshot.ssh,
                ("zfs", "get", "-H", "-o", "value", "guid", snapshot.name),
            ),
            capture_output=True,
        )
        try:
            return int((result.stdout or "").strip())
        except ValueError as error:
            raise ZFSDriverError(f"could not read ZFS snapshot GUID: {snapshot.name}") from error

    def format_locator(self, artifact: bckp.BackupArtifact[ZFSSnapshot]) -> str:
        snapshot = artifact.representation
        return (
            snapshot.name if snapshot.ssh is None else snapshot.ssh.format_location(snapshot.name)
        )

    def cap_delete(
        self,
        artifacts: ty.Sequence[bckp.BackupArtifact[ZFSSnapshot]],
    ) -> None:
        snapshots = tuple(artifact.representation for artifact in artifacts)
        if any(not self._owns(snapshot) for snapshot in snapshots):
            raise ZFSDriverError("ZFS artifact does not belong to the destination dataset")
        self._destroy(snapshots)

    def cap_cleanup(self, representation: ZFSSnapshot) -> None:
        self._destroy((representation,))

    def _destroy(
        self,
        snapshots: ty.Sequence[ZFSSnapshot],
        *,
        check: bool = True,
    ) -> None:
        if not snapshots:
            return
        first = snapshots[0]
        names = ",".join(snapshot.snapshot for snapshot in snapshots)
        self.runner.run(
            command_for_ssh(
                first.ssh,
                ("zfs", "destroy", f"{first.dataset}@{names}"),
            ),
            check=check,
        )

    def _require_encrypted(
        self,
        dataset: str,
        ssh: SSHTarget | None,
    ) -> None:
        result = self.runner.run(
            command_for_ssh(
                ssh,
                ("zfs", "get", "-H", "-o", "value", "encryption", dataset),
            ),
            capture_output=True,
        )
        if failure := _encryption_failure(dataset, result):
            raise ZFSDriverError(failure)


def _dataset_valid(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and not value.startswith("/")
        and not value.endswith("/")
        and "//" not in value
        and "@" not in value
        and "#" not in value
    )


def _encryption_failure(dataset: str, result: CommandResult) -> str | None:
    if (result.stdout or "").strip() in {"", "-", "off"}:
        return f"ZFS dataset is not encrypted: {dataset}"
    return None
