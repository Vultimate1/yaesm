"""Tests for yaesm.driver.btrfsdriver."""

import dataclasses
import subprocess
from datetime import datetime
from uuid import UUID

import pytest
import voluptuous as vlp

import yaesm.command as command_module
import yaesm.ty as ty
from yaesm.backup import Backup, BackupArtifact, BackupOperation
from yaesm.check import CheckRole
from yaesm.command import Command, CommandResult, CommandRunner, CommandStage, PipelineCommand
from yaesm.driver.btrfsdriver import (
    BtrfsDriver,
    BtrfsDriverError,
    BtrfsSnapshot,
    BtrfsStream,
    BtrfsSubvolume,
)
from yaesm.representation import CommandStream, DataProperty, PathTree
from yaesm.ssh import SSHTarget, command_for_ssh

_BTRFS_SEND = ("btrfs", "send", "--compressed-data")
_SNAPSHOT_UUID = UUID("11111111-1111-1111-1111-111111111111")


def _snapshot(
    path: ty.Path,
    ssh: SSHTarget | None = None,
    *,
    uuid: UUID = _SNAPSHOT_UUID,
    source_uuid: UUID | None = None,
) -> BtrfsSnapshot:
    return BtrfsSnapshot(path, ssh, uuid=uuid, source_uuid=source_uuid)


def _snapshot_output(
    uuid: UUID = _SNAPSHOT_UUID,
    parent_uuid: UUID | None = None,
    received_uuid: UUID | None = None,
) -> str:
    return (
        f"UUID: {uuid}\nParent UUID: {parent_uuid or '-'}\nReceived UUID: {received_uuid or '-'}\n"
    )


class RecordingRunner(CommandRunner):
    def __init__(
        self,
        returncodes: ty.Iterable[int] = (),
        stdouts: ty.Iterable[str | None] = (),
        pipeline_failures: ty.Iterable[BaseException | None] = (),
    ) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.pipelines: list[tuple[tuple[str, ...], ...]] = []
        self.returncodes = list(returncodes)
        self.stdouts = list(stdouts)
        self.pipeline_failures = list(pipeline_failures)

    def run(
        self,
        command: Command,
        *,
        capture_output: bool = False,
        check: bool = True,
    ) -> CommandResult:
        normalized = tuple(str(argument) for argument in command)
        self.commands.append(normalized)
        returncode = self.returncodes.pop(0) if self.returncodes else 0
        stdout = self.stdouts.pop(0) if self.stdouts else None
        if (
            stdout is None
            and returncode == 0
            and capture_output
            and "btrfs subvolume show" in " ".join(normalized)
        ):
            stdout = _snapshot_output()
        return CommandResult(stdout, "", (returncode,))

    def pipeline(
        self,
        commands: ty.Sequence[PipelineCommand],
        *,
        capture_output: bool = False,
        check: bool = True,
    ) -> CommandResult:
        pipeline = tuple(
            command.execution_command()
            if isinstance(command, CommandStage)
            else tuple(str(argument) for argument in command)
            for command in commands
        )
        self.pipelines.append(pipeline)
        if self.pipeline_failures:
            failure = self.pipeline_failures.pop(0)
            if failure is not None:
                raise failure
        return CommandResult(None, "", (0,) * len(pipeline))


class BtrfsStateRunner(CommandRunner):
    """Model the small amount of Btrfs state used by rolling-base tests."""

    def __init__(self, source: ty.Path, destination: ty.Path) -> None:
        self.source = source
        self.destination = destination
        self.commands: list[tuple[str, ...]] = []
        self.pipelines: list[tuple[tuple[str, ...], ...]] = []
        self.snapshots: dict[ty.Path, BtrfsSnapshot] = {}
        self.next_uuid = 1
        self.pipeline_failure: BaseException | None = None

    def _uuid(self) -> UUID:
        value = UUID(int=self.next_uuid)
        self.next_uuid += 1
        return value

    def run(
        self,
        command: Command,
        *,
        capture_output: bool = False,
        check: bool = True,
    ) -> CommandResult:
        normalized = tuple(str(argument) for argument in command)
        self.commands.append(normalized)
        if normalized[:4] == ("btrfs", "subvolume", "snapshot", "-r"):
            source, destination = map(ty.Path, normalized[4:])
            if destination.parent == self.destination and destination.name.startswith("yaesm-"):
                return CommandResult(None, "", (1,))
            parent = self.snapshots.get(source)
            self.snapshots[destination] = BtrfsSnapshot(
                destination,
                uuid=self._uuid(),
                source_uuid=None if parent is None else parent.uuid,
            )
            return CommandResult(None, "", (0,))
        if normalized[:3] == ("btrfs", "subvolume", "show"):
            snapshot = self.snapshots.get(ty.Path(normalized[3]))
            if snapshot is None:
                return CommandResult(None, "", (1,))
            return CommandResult(
                _snapshot_output(snapshot.uuid, received_uuid=snapshot.source_uuid),
                "",
                (0,),
            )
        if normalized[:3] == ("btrfs", "subvolume", "list"):
            lines = (
                "ID 256 parent_uuid - "
                f"received_uuid {snapshot.source_uuid or '-'} uuid {snapshot.uuid} "
                f"path {snapshot.path.name}"
                for snapshot in self.snapshots.values()
                if snapshot.path.parent == self.destination
            )
            return CommandResult("\n".join(lines), "", (0,))
        if normalized[:3] == ("btrfs", "subvolume", "delete"):
            for path in normalized[3:]:
                self.snapshots.pop(ty.Path(path), None)
            return CommandResult(None, "", (0,))
        if normalized[:3] == ("mv", "-T", "--"):
            source, destination = map(ty.Path, normalized[3:])
            snapshot = self.snapshots.pop(source)
            self.snapshots[destination] = dataclasses.replace(snapshot, path=destination)
            return CommandResult(None, "", (0,))
        return CommandResult(None, "", (0,))

    def pipeline(
        self,
        commands: ty.Sequence[PipelineCommand],
        *,
        capture_output: bool = False,
        check: bool = True,
    ) -> CommandResult:
        pipeline = tuple(
            command.execution_command()
            if isinstance(command, CommandStage)
            else tuple(str(argument) for argument in command)
            for command in commands
        )
        self.pipelines.append(pipeline)
        if self.pipeline_failure is not None:
            raise self.pipeline_failure
        source = self.snapshots[ty.Path(pipeline[0][-1])]
        received = self.destination / source.path.name
        self.snapshots[received] = BtrfsSnapshot(
            received,
            uuid=self._uuid(),
            source_uuid=source.uuid,
        )
        return CommandResult(None, "", (0,) * len(pipeline))


def with_runner(driver: BtrfsDriver, runner: CommandRunner) -> BtrfsDriver:
    driver.runner = runner
    return driver


def operation(*, previous_backup_names: tuple[str, ...] = ()) -> BackupOperation:
    return BackupOperation(
        "example",
        "manual",
        datetime(2026, 8, 27, 12, 30),
        previous_backup_names=previous_backup_names,
    )


def test_name():
    assert BtrfsDriver.name() == "btrfs"


def test_config_schema(tmp_path):
    assert BtrfsDriver.config_schema()({"location": str(tmp_path)}) == {"location": tmp_path}


def test_config_schema_accepts_path_location(tmp_path):
    assert BtrfsDriver.config_schema()({"location": tmp_path})["location"] == tmp_path


def test_config_schema_accepts_shorthand(tmp_path):
    assert BtrfsDriver.config_schema()(tmp_path) == {"location": tmp_path}


@pytest.mark.parametrize("location", [None, 42])
def test_config_schema_rejects_invalid_location_type(location):
    with pytest.raises(vlp.Invalid, match="location must be a path"):
        BtrfsDriver.config_schema()({"location": location})


def test_config_schema_output_constructs_driver(tmp_path):
    config = BtrfsDriver.config_schema()({"location": tmp_path})

    driver = BtrfsDriver(**config)

    assert driver.location == tmp_path
    assert driver.ssh is None


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"location": "relative"},
        {"location": "/tmp", "ssh": "ssh://host"},
        {"location": "/tmp", "bootstrap_refresh_days": 21},
        {"location": "/tmp", "x": 1},
    ],
)
def test_config_schema_rejects_invalid_config(config):
    with pytest.raises(vlp.Invalid):
        BtrfsDriver.config_schema()(config)


def test_cap_source(tmp_path):
    driver = BtrfsDriver(tmp_path)
    assert driver.capabilities() == {
        "source",
        "store",
        "snapshot",
        "export",
        "import",
        "list",
        "delete",
        "cleanup",
    }
    assert driver.capability_metadata("store").base == "source"
    assert driver.capability_metadata("store").adds == {DataProperty.SNAPSHOT}
    assert driver.cap_source() == BtrfsSubvolume(tmp_path)


def test_cap_source_includes_ssh_target(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")

    assert BtrfsDriver(tmp_path, target).cap_source() == BtrfsSubvolume(tmp_path, target)


def test_checks_directory_requirements(tmp_path, monkeypatch):
    runner = RecordingRunner()
    monkeypatch.setattr(command_module, "run", runner.run)
    driver = BtrfsDriver(tmp_path)

    checks = driver.check(CheckRole.SOURCE)

    assert tuple(check.description for check in checks) == (
        "btrfs is installed",
        f"directory exists: {tmp_path}",
        f"directory is a Btrfs subvolume: {tmp_path}",
        f"directory is readable: {tmp_path}",
        f"directory is writable: {tmp_path}",
        f"directory is searchable: {tmp_path}",
    )
    assert runner.commands == []
    assert all(check.run().passed for check in checks)
    assert runner.commands == [
        ("btrfs", "--version"),
        ("test", "-d", str(tmp_path)),
        ("btrfs", "subvolume", "show", str(tmp_path)),
        ("test", "-r", str(tmp_path)),
        ("test", "-w", str(tmp_path)),
        ("test", "-x", str(tmp_path)),
    ]


def test_checks_remote_directory_requirements(tmp_path, monkeypatch):
    target = SSHTarget("ssh://host", tmp_path / "key")
    runner = RecordingRunner()
    monkeypatch.setattr(command_module, "run", runner.run)
    driver = BtrfsDriver(tmp_path, target)

    checks = driver.check(CheckRole.DESTINATION)
    for check in checks:
        check.run()

    assert tuple(check.description for check in checks) == (
        f"btrfs is installed on {target}",
        f"directory exists: {tmp_path} on {target}",
        f"directory is on a Btrfs filesystem: {tmp_path} on {target}",
        f"directory is readable: {tmp_path} on {target}",
        f"directory is writable: {tmp_path} on {target}",
        f"directory is searchable: {tmp_path} on {target}",
    )
    assert runner.commands == [
        target.openssh_command(("btrfs", "--version")),
        target.openssh_command(("test", "-d", tmp_path)),
        target.openssh_command(("btrfs", "filesystem", "usage", tmp_path)),
        target.openssh_command(("test", "-r", tmp_path)),
        target.openssh_command(("test", "-w", tmp_path)),
        target.openssh_command(("test", "-x", tmp_path)),
    ]


def test_artifact_source_checks_storage_read_requirements(tmp_path):
    checks = BtrfsDriver(tmp_path)._checks(CheckRole.ARTIFACT_SOURCE)

    assert tuple(check.description for check in checks) == (
        f"directory exists: {tmp_path}",
        f"directory is on a Btrfs filesystem: {tmp_path}",
        f"directory is readable: {tmp_path}",
        f"directory is searchable: {tmp_path}",
    )


def test_remote_check_failure_names_logical_executable(tmp_path, monkeypatch):
    target = SSHTarget("ssh://host", tmp_path / "key")
    runner = RecordingRunner((1,))
    monkeypatch.setattr(command_module, "run", runner.run)
    driver = BtrfsDriver(tmp_path, target)

    result = driver.check(CheckRole.SOURCE)[0].run()

    assert result.failure == "btrfs exited with status 1"


@pytest.mark.parametrize(
    ("role", "index", "executable"),
    [
        (CheckRole.SOURCE, 0, "test"),
        (CheckRole.SOURCE, 1, "btrfs"),
        (CheckRole.SOURCE, 2, "test"),
        (CheckRole.SOURCE, 3, "test"),
        (CheckRole.SOURCE, 4, "test"),
        (CheckRole.ARTIFACT_SOURCE, 0, "test"),
        (CheckRole.ARTIFACT_SOURCE, 1, "btrfs"),
        (CheckRole.ARTIFACT_SOURCE, 2, "test"),
        (CheckRole.ARTIFACT_SOURCE, 3, "test"),
        (CheckRole.DESTINATION, 0, "test"),
        (CheckRole.DESTINATION, 1, "btrfs"),
        (CheckRole.DESTINATION, 2, "test"),
        (CheckRole.DESTINATION, 3, "test"),
        (CheckRole.DESTINATION, 4, "test"),
    ],
)
def test_each_directory_check_reports_failure(role, index, executable, tmp_path, monkeypatch):
    monkeypatch.setattr(
        command_module,
        "run",
        lambda *args, **kwargs: CommandResult(None, "permission denied", (5,)),
    )
    check = BtrfsDriver(tmp_path)._checks(role)[index]

    result = check.run()

    assert result.description == check.description
    assert result.passed is False
    assert result.failure == f"{executable} exited with status 5"
    assert result.stderr == "permission denied"


def test_transform_check_does_not_validate_unused_directory(tmp_path):
    driver = BtrfsDriver(tmp_path)
    checks = driver.check(CheckRole.TRANSFORM)

    assert tuple(check.description for check in checks) == ("btrfs is installed",)
    assert driver._checks(CheckRole.TRANSFORM) == ()


def test_cap_snapshot(tmp_path):
    runner = RecordingRunner()
    driver = with_runner(BtrfsDriver(tmp_path), runner)
    source = driver.cap_source()

    snapshot = driver.cap_snapshot(source)

    assert snapshot.path.parent == source.path
    assert snapshot.path.name.startswith(".yaesm-btrfs-staging-")
    assert snapshot.ssh is None
    assert snapshot.uuid == _SNAPSHOT_UUID
    assert runner.commands == [
        ("btrfs", "subvolume", "snapshot", "-r", str(source.path), str(snapshot.path)),
        ("btrfs", "subvolume", "show", str(snapshot.path)),
    ]


def test_cap_snapshot_remote(tmp_path):
    runner = RecordingRunner()
    target = SSHTarget("ssh://host", tmp_path / "key")
    driver = with_runner(BtrfsDriver(tmp_path, target), runner)

    snapshot = driver.cap_snapshot(driver.cap_source())

    remote_command = ("btrfs", "subvolume", "snapshot", "-r", tmp_path, snapshot.path)
    assert snapshot.ssh is target
    assert runner.commands == [
        target.openssh_command(remote_command),
        target.openssh_command(("btrfs", "subvolume", "show", snapshot.path)),
    ]


@pytest.mark.parametrize(
    ("parent_uuid", "received_uuid", "source_uuid"),
    [
        (
            UUID("22222222-2222-2222-2222-222222222222"),
            None,
            UUID("22222222-2222-2222-2222-222222222222"),
        ),
        (
            UUID("22222222-2222-2222-2222-222222222222"),
            UUID("33333333-3333-3333-3333-333333333333"),
            UUID("33333333-3333-3333-3333-333333333333"),
        ),
        (None, None, None),
    ],
)
def test_cap_snapshot_reads_source_uuid(tmp_path, parent_uuid, received_uuid, source_uuid):
    runner = RecordingRunner(
        stdouts=(None, _snapshot_output(parent_uuid=parent_uuid, received_uuid=received_uuid))
    )
    driver = with_runner(BtrfsDriver(tmp_path), runner)

    snapshot = driver.cap_snapshot(driver.cap_source())

    assert snapshot.source_uuid == source_uuid


@pytest.mark.parametrize(
    "output",
    [
        "",
        "UUID: -\n",
        "UUID: not-a-uuid\n",
        "Parent UUID: value\n",
        f"UUID: {_SNAPSHOT_UUID}\nParent UUID: not-a-uuid\n",
    ],
)
def test_cap_snapshot_rejects_invalid_uuid_metadata(tmp_path, output):
    runner = RecordingRunner(stdouts=(None, output))
    driver = with_runner(BtrfsDriver(tmp_path), runner)

    with pytest.raises(BtrfsDriverError, match="could not read Btrfs snapshot UUID"):
        driver.cap_snapshot(driver.cap_source())

    assert runner.commands[-1][:3] == ("btrfs", "subvolume", "delete")


def test_cap_store_uses_direct_snapshot_on_same_endpoint(tmp_path):
    runner = RecordingRunner()
    source = BtrfsSubvolume(tmp_path / "source")
    driver = with_runner(BtrfsDriver(tmp_path / "destination"), runner)

    artifact = driver.cap_store(source, operation())

    destination = tmp_path / "destination" / operation().artifact_name
    assert artifact == BackupArtifact(operation(), _snapshot(destination))
    assert runner.commands == [
        (
            "btrfs",
            "subvolume",
            "snapshot",
            "-r",
            str(source.path),
            str(destination),
        ),
        ("btrfs", "subvolume", "show", str(destination)),
    ]
    assert runner.pipelines == []


def test_cap_store_falls_back_to_send_receive(tmp_path):
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    runner = BtrfsStateRunner(source_dir, destination_dir)
    source = BtrfsSubvolume(source_dir)
    driver = with_runner(BtrfsDriver(destination_dir), runner)

    artifact = driver.cap_store(source, operation())

    pending = source.path / ".yaesm-btrfs-pending-example"
    base = source.path / ".yaesm-btrfs-base-example"
    destination = destination_dir / operation().artifact_name
    assert artifact.representation.path == destination
    assert runner.pipelines == [
        (
            (*_BTRFS_SEND, str(pending)),
            ("btrfs", "receive", str(destination_dir)),
        )
    ]
    assert base in runner.snapshots
    assert runner.snapshots[destination].source_uuid == runner.snapshots[base].uuid
    assert pending not in runner.snapshots


def test_backup_execute_uses_readonly_snapshot_with_incremental_send_fallback(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    runner = BtrfsStateRunner(source, destination)
    backup = Backup(
        "example",
        BtrfsDriver(source),
        with_runner(BtrfsDriver(destination), runner),
    )

    artifact = backup.execute("manual", operation().created_at)

    pending = source / ".yaesm-btrfs-pending-example"
    base = source / ".yaesm-btrfs-base-example"
    stored = destination / operation().artifact_name
    assert artifact.representation.path == stored
    assert runner.commands[1] == (
        "btrfs",
        "subvolume",
        "snapshot",
        "-r",
        str(source),
        str(stored),
    )
    assert (
        "btrfs",
        "subvolume",
        "snapshot",
        "-r",
        str(source),
        str(pending),
    ) in runner.commands
    assert runner.pipelines[-1] == (
        (*_BTRFS_SEND, str(pending)),
        ("btrfs", "receive", str(destination)),
    )
    assert base in runner.snapshots


def test_cap_store_uses_explicit_base_without_rolling_it(tmp_path):
    runner = RecordingRunner((1,))
    source = BtrfsSubvolume(tmp_path / "source")
    base = _snapshot(source.path / "base")
    destination_dir = tmp_path / "destination"

    with_runner(BtrfsDriver(destination_dir), runner).cap_store(source, operation(), base)

    staging = ty.Path(runner.commands[1][-1])
    assert runner.pipelines == [
        (
            (*_BTRFS_SEND, "-p", str(base.path), str(staging)),
            ("btrfs", "receive", str(destination_dir)),
        )
    ]
    assert all(".yaesm-btrfs-base" not in " ".join(command) for command in runner.commands)


def test_cap_store_existing_snapshot_uses_full_send_without_rolling_base(tmp_path):
    runner = RecordingRunner()
    target = SSHTarget("ssh://source", tmp_path / "key")
    source = _snapshot(tmp_path / "source", target)
    destination = tmp_path / "destination"

    artifact = with_runner(BtrfsDriver(destination), runner).cap_store(source, operation())

    assert artifact.representation.path == destination / operation().artifact_name
    assert runner.pipelines == [
        (
            target.openssh_command((*_BTRFS_SEND, source.path)),
            ("btrfs", "receive", str(destination)),
        )
    ]
    assert not any(".yaesm-btrfs-base" in " ".join(command) for command in runner.commands)


@pytest.mark.parametrize(
    ("source_spec", "destination_spec"),
    [
        (None, "ssh://destination"),
        ("ssh://source", None),
        ("ssh://source", "ssh://destination"),
    ],
)
def test_cap_store_sends_between_different_endpoints(
    tmp_path,
    source_spec,
    destination_spec,
):
    source_target = None if source_spec is None else SSHTarget(source_spec, tmp_path / "source-key")
    destination_target = (
        None
        if destination_spec is None
        else SSHTarget(destination_spec, tmp_path / "destination-key")
    )
    source = BtrfsSubvolume(tmp_path / "source", source_target)
    destination_dir = tmp_path / "destination"
    runner = RecordingRunner((1, 1, 0, 0, 0, 0, 1))

    with_runner(BtrfsDriver(destination_dir, destination_target), runner).cap_store(
        source,
        operation(),
    )

    pending = source.path / ".yaesm-btrfs-pending-example"
    receive = command_for_ssh(
        destination_target,
        ("btrfs", "receive", destination_dir),
    )
    assert runner.pipelines[0] == (
        command_for_ssh(source_target, (*_BTRFS_SEND, pending)),
        receive,
    )


def test_cap_store_uses_direct_snapshot_on_same_remote_endpoint(tmp_path):
    source_target = SSHTarget("ssh://host", tmp_path / "source-key")
    destination_target = SSHTarget("ssh://host", tmp_path / "destination-key")
    source = BtrfsSubvolume(tmp_path / "source", source_target)
    destination_dir = tmp_path / "destination"
    runner = RecordingRunner()

    artifact = with_runner(BtrfsDriver(destination_dir, destination_target), runner).cap_store(
        source,
        operation(),
    )

    destination = destination_dir / operation().artifact_name
    assert artifact == BackupArtifact(
        operation(),
        _snapshot(destination, destination_target),
    )
    assert runner.commands == [
        destination_target.openssh_command(
            ("btrfs", "subvolume", "snapshot", "-r", source.path, destination)
        ),
        destination_target.openssh_command(("btrfs", "subvolume", "show", destination)),
    ]
    assert runner.pipelines == []


def test_cap_store_rolls_matching_incremental_base(tmp_path):
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    runner = BtrfsStateRunner(source_dir, destination_dir)
    driver = with_runner(BtrfsDriver(destination_dir), runner)
    source = BtrfsSubvolume(source_dir)
    first_operation = operation()
    second_operation = dataclasses.replace(
        first_operation,
        created_at=datetime(2026, 8, 27, 13, 30),
    )

    first = driver.cap_store(source, first_operation)
    first_base_uuid = runner.snapshots[source_dir / ".yaesm-btrfs-base-example"].uuid
    second = driver.cap_store(source, second_operation)

    base = source_dir / ".yaesm-btrfs-base-example"
    pending = source_dir / ".yaesm-btrfs-pending-example"
    assert runner.pipelines[-1] == (
        (*_BTRFS_SEND, "-p", str(base), str(pending)),
        ("btrfs", "receive", str(destination_dir)),
    )
    assert first_base_uuid != runner.snapshots[base].uuid
    assert runner.snapshots[first.representation.path].source_uuid == first_base_uuid
    assert runner.snapshots[second.representation.path].source_uuid == runner.snapshots[base].uuid


def test_cap_store_rolls_base_under_previous_backup_name(tmp_path):
    source_dir = tmp_path / "source"
    destination = tmp_path / "destination"
    runner = BtrfsStateRunner(source_dir, destination)
    source = BtrfsSubvolume(source_dir)
    driver = with_runner(BtrfsDriver(destination), runner)
    old_operation = dataclasses.replace(operation(), backup_name="old-example")
    driver.cap_store(source, old_operation)
    new_operation = dataclasses.replace(
        operation(previous_backup_names=("old-example",)),
        created_at=datetime(2026, 8, 27, 13, 30),
    )

    driver.cap_store(source, new_operation)

    old_base = source_dir / ".yaesm-btrfs-base-old-example"
    new_base = source_dir / ".yaesm-btrfs-base-example"
    assert runner.pipelines[-1][0] == (
        *_BTRFS_SEND,
        "-p",
        str(old_base),
        str(source_dir / ".yaesm-btrfs-pending-example"),
    )
    assert old_base not in runner.snapshots
    assert new_base in runner.snapshots


def test_cap_store_uses_full_send_for_unpaired_base(tmp_path):
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    runner = BtrfsStateRunner(source_dir, destination_dir)
    base = source_dir / ".yaesm-btrfs-base-example"
    runner.snapshots[base] = _snapshot(base, uuid=UUID(int=100))
    unrelated = destination_dir / operation().artifact_name
    runner.snapshots[unrelated] = _snapshot(
        unrelated,
        uuid=UUID(int=101),
        source_uuid=UUID(int=102),
    )

    with_runner(BtrfsDriver(destination_dir), runner).cap_store(
        BtrfsSubvolume(source_dir),
        dataclasses.replace(operation(), created_at=datetime(2026, 8, 27, 13, 30)),
    )

    assert runner.pipelines[-1][0] == (
        *_BTRFS_SEND,
        str(source_dir / ".yaesm-btrfs-pending-example"),
    )


def test_cap_store_keeps_old_base_when_receive_fails(tmp_path):
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    runner = BtrfsStateRunner(source_dir, destination_dir)
    driver = with_runner(BtrfsDriver(destination_dir), runner)
    source = BtrfsSubvolume(source_dir)
    driver.cap_store(source, operation())
    base = source_dir / ".yaesm-btrfs-base-example"
    base_uuid = runner.snapshots[base].uuid
    runner.pipeline_failure = RuntimeError("receive failed")

    with pytest.raises(RuntimeError, match="receive failed"):
        driver.cap_store(
            source,
            dataclasses.replace(operation(), created_at=datetime(2026, 8, 27, 13, 30)),
        )

    assert runner.snapshots[base].uuid == base_uuid
    assert source_dir / ".yaesm-btrfs-pending-example" not in runner.snapshots


def test_cap_store_recovers_received_pending_base(tmp_path):
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    runner = BtrfsStateRunner(source_dir, destination_dir)
    source = BtrfsSubvolume(source_dir)
    old_base = source_dir / ".yaesm-btrfs-base-example"
    pending = source_dir / ".yaesm-btrfs-pending-example"
    runner.snapshots[old_base] = _snapshot(old_base, uuid=UUID(int=100))
    runner.snapshots[pending] = _snapshot(pending, uuid=UUID(int=101))
    received_operation = operation()
    received = destination_dir / received_operation.artifact_name
    runner.snapshots[received] = _snapshot(
        received,
        uuid=UUID(int=102),
        source_uuid=UUID(int=101),
    )
    new_operation = dataclasses.replace(
        operation(),
        created_at=datetime(2026, 8, 27, 13, 30),
    )

    with_runner(BtrfsDriver(destination_dir), runner).cap_store(source, new_operation)

    base = source_dir / ".yaesm-btrfs-base-example"
    assert old_base == base
    assert runner.pipelines[-1][0] == (
        *_BTRFS_SEND,
        "-p",
        str(base),
        str(pending),
    )


def test_cap_export_full(tmp_path):
    snapshot = _snapshot(tmp_path / "snapshot")

    stream = BtrfsDriver(tmp_path).cap_export(snapshot)

    assert stream.stages == (CommandStage((*_BTRFS_SEND, snapshot.path)),)
    assert stream.subvolume_name == "snapshot"
    assert stream.base_uuid is None
    assert stream.suffixes == (".btrfs",)


def test_cap_export_incremental(tmp_path):
    base = _snapshot(tmp_path / "base")
    snapshot = _snapshot(tmp_path / "snapshot")

    stream = BtrfsDriver(tmp_path).cap_export(snapshot, base)

    assert stream.stages == (CommandStage((*_BTRFS_SEND, "-p", base.path, snapshot.path)),)
    assert stream.base_uuid == base.uuid


def test_cap_export_remote(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")
    snapshot = _snapshot(tmp_path / "snapshot", target)

    stream = BtrfsDriver(tmp_path).cap_export(snapshot)

    assert stream.stages == (CommandStage((*_BTRFS_SEND, snapshot.path), target),)


def test_cap_export_rejects_base_on_different_endpoint(tmp_path):
    source_target = SSHTarget("ssh://source", tmp_path / "key")
    base_target = SSHTarget("ssh://base", tmp_path / "key")
    snapshot = _snapshot(tmp_path / "snapshot", source_target)
    base = _snapshot(tmp_path / "base", base_target)

    with pytest.raises(BtrfsDriverError, match="different SSH endpoints"):
        BtrfsDriver(tmp_path).cap_export(snapshot, base)


def test_incremental_base_requires_matching_btrfs_uuid_pair(tmp_path):
    source_target = SSHTarget("ssh://source", tmp_path / "key")
    destination_target = SSHTarget("ssh://destination", tmp_path / "key")
    source = _snapshot(tmp_path / "source", source_target, uuid=UUID(int=1))
    source_base = _snapshot(tmp_path / "source-base", source_target, uuid=UUID(int=2))
    destination_base = _snapshot(
        tmp_path / "destination-base",
        destination_target,
        uuid=UUID(int=3),
        source_uuid=source_base.uuid,
    )

    assert BtrfsDriver(tmp_path, source_target).validate_base(
        "export", source, source_base, destination_base
    )
    assert BtrfsDriver(tmp_path, destination_target).validate_base(
        "store", source, source_base, destination_base
    )
    assert not BtrfsDriver(tmp_path, source_target).validate_base(
        "export",
        source,
        source_base,
        dataclasses.replace(destination_base, source_uuid=UUID(int=4)),
    )
    assert not BtrfsDriver(tmp_path, destination_target).validate_base(
        "store",
        source,
        source_base,
        dataclasses.replace(destination_base, ssh=source_target),
    )
    assert not BtrfsDriver(tmp_path, source_target).validate_base(
        "export",
        PathTree(source.path, source_target),
        source_base,
        destination_base,
    )


def test_incremental_btrfs_import_requires_matching_stream_and_destination(tmp_path):
    target = SSHTarget("ssh://destination", tmp_path / "key")
    source_base = _snapshot(tmp_path / "source-base", uuid=UUID(int=1))
    destination_base = _snapshot(
        tmp_path / "destination-base",
        target,
        uuid=UUID(int=2),
        source_uuid=source_base.uuid,
    )
    stream = BtrfsStream((), subvolume_name="snapshot", base_uuid=source_base.uuid)
    driver = BtrfsDriver(tmp_path, target)

    assert driver.validate_base("import", stream, source_base, destination_base)
    assert not driver.validate_base(
        "import",
        dataclasses.replace(stream, base_uuid=UUID(int=3)),
        source_base,
        destination_base,
    )


def test_cap_import_rejects_base_on_different_endpoint(tmp_path):
    destination_target = SSHTarget("ssh://destination", tmp_path / "key")
    base_target = SSHTarget("ssh://base", tmp_path / "key")
    stream = BtrfsStream(
        (CommandStage(("btrfs", "send", tmp_path / "snapshot")),),
        subvolume_name="snapshot",
    )
    base = _snapshot(tmp_path / "base", base_target)

    with pytest.raises(BtrfsDriverError, match="different SSH endpoints"):
        BtrfsDriver(tmp_path, destination_target).cap_import(stream, operation(), base)


def test_cap_import_cleans_up_failed_receive(tmp_path):
    runner = RecordingRunner(
        pipeline_failures=(RuntimeError("receive failed"),),
    )
    stream = BtrfsStream(
        (CommandStage(("btrfs", "send", tmp_path / "snapshot")),),
        subvolume_name="snapshot",
    )

    with pytest.raises(RuntimeError, match="receive failed"):
        with_runner(BtrfsDriver(tmp_path), runner).cap_import(stream, operation())

    assert runner.commands == [("btrfs", "subvolume", "delete", str(tmp_path / "snapshot"))]


def test_cap_import_reads_received_snapshot_uuid(tmp_path):
    received_uuid = UUID("22222222-2222-2222-2222-222222222222")
    runner = RecordingRunner(
        stdouts=(None, _snapshot_output(received_uuid=received_uuid)),
    )
    stream = BtrfsStream(
        (CommandStage(("btrfs", "send", tmp_path / "snapshot")),),
        subvolume_name="snapshot",
    )

    artifact = with_runner(BtrfsDriver(tmp_path), runner).cap_import(stream, operation())

    assert artifact.representation.uuid == _SNAPSHOT_UUID
    assert artifact.representation.source_uuid == received_uuid


def test_cap_list_returns_matching_artifacts_newest_first(tmp_path):
    destination = tmp_path / "destination"
    older = BackupOperation("example", "hourly", datetime(2026, 8, 27, 12, 30))
    source_uuid = UUID("11111111-1111-1111-1111-111111111111")
    older_uuid = UUID("22222222-2222-2222-2222-222222222222")
    newer_uuid = UUID("33333333-3333-3333-3333-333333333333")
    newer = BackupOperation(
        "example",
        "hourly",
        datetime(2026, 8, 27, 13, 30),
        str(source_uuid),
    )
    runner = RecordingRunner(
        stdouts=(
            "\n".join(
                (
                    "ID 256 gen 1 top level 5 parent_uuid - received_uuid - "
                    f"uuid {older_uuid} path snapshots/{older.artifact_name}",
                    "ID 257 gen 1 top level 5 parent_uuid - received_uuid - "
                    "uuid 44444444-4444-4444-4444-444444444444 "
                    "path snapshots/not-an-artifact",
                    "ID 258 gen 1 top level 5 parent_uuid - "
                    f"received_uuid {source_uuid} uuid {newer_uuid} "
                    f"path snapshots/{newer.artifact_name}",
                )
            ),
        )
    )

    artifacts = with_runner(BtrfsDriver(destination), runner).cap_list("example")

    assert artifacts == (
        BackupArtifact(
            newer,
            _snapshot(
                destination / newer.artifact_name,
                uuid=newer_uuid,
                source_uuid=source_uuid,
            ),
        ),
        BackupArtifact(
            older,
            _snapshot(destination / older.artifact_name, uuid=older_uuid),
        ),
    )
    assert runner.commands == [
        (
            "btrfs",
            "subvolume",
            "list",
            "-u",
            "-q",
            "-R",
            "-o",
            str(destination),
        )
    ]


def test_cap_list_uses_parent_uuid_for_local_snapshot(tmp_path):
    operation_ = operation()
    snapshot_uuid = UUID("11111111-1111-1111-1111-111111111111")
    parent_uuid = UUID("22222222-2222-2222-2222-222222222222")
    runner = RecordingRunner(
        stdouts=(
            "ID 256 gen 1 top level 5 "
            f"parent_uuid {parent_uuid} received_uuid - uuid {snapshot_uuid} "
            f"path {operation_.artifact_name}",
        )
    )

    (artifact,) = with_runner(BtrfsDriver(tmp_path), runner).cap_list("example")

    assert artifact.operation.source_artifact_id == str(parent_uuid)
    assert artifact.representation.source_uuid == parent_uuid
    assert BtrfsDriver(tmp_path).artifact_id(artifact) == str(snapshot_uuid)


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "not Btrfs output",
        "ID 256 uuid missing path snapshot",
        "ID 256 parent_uuid - received_uuid - uuid - path snapshot",
        "ID 256 parent_uuid - received_uuid - uuid invalid path snapshot",
        f"ID 256 parent_uuid invalid received_uuid - uuid {_SNAPSHOT_UUID} path snapshot",
    ],
)
def test_cap_list_ignores_malformed_output(tmp_path, stdout):
    runner = RecordingRunner(stdouts=(stdout,))

    assert with_runner(BtrfsDriver(tmp_path), runner).cap_list("example") == ()


def test_cap_list_remote(tmp_path):
    runner = RecordingRunner()
    target = SSHTarget("ssh://host", tmp_path / "key")
    destination = tmp_path / "destination"

    assert with_runner(BtrfsDriver(destination, target), runner).cap_list("example") == ()
    assert runner.commands == [
        target.openssh_command(
            (
                "btrfs",
                "subvolume",
                "list",
                "-u",
                "-q",
                "-R",
                "-o",
                destination,
            )
        )
    ]


def test_formats_local_and_remote_artifact_locators(tmp_path):
    operation_ = operation()
    path = tmp_path / operation_.artifact_name
    target = SSHTarget("ssh://host", tmp_path / "key")
    driver = BtrfsDriver(tmp_path)

    assert driver.format_locator(BackupArtifact(operation_, _snapshot(path))) == str(path)
    assert driver.format_locator(
        BackupArtifact(operation_, _snapshot(path, target))
    ) == target.format_location(path)


def test_cap_delete_batches_artifacts(tmp_path):
    runner = RecordingRunner()
    artifacts = (
        BackupArtifact(operation(), _snapshot(tmp_path / "one")),
        BackupArtifact(operation(), _snapshot(tmp_path / "two")),
    )

    with_runner(BtrfsDriver(tmp_path), runner).cap_delete(artifacts)

    assert runner.commands == [
        (
            "btrfs",
            "subvolume",
            "delete",
            str(tmp_path / "one"),
            str(tmp_path / "two"),
        )
    ]


def test_cap_delete_batches_remote_artifacts(tmp_path):
    runner = RecordingRunner()
    target = SSHTarget("ssh://host", tmp_path / "key")
    artifacts = (
        BackupArtifact(operation(), _snapshot(tmp_path / "one", target)),
        BackupArtifact(operation(), _snapshot(tmp_path / "two", target)),
    )

    with_runner(BtrfsDriver(tmp_path, target), runner).cap_delete(artifacts)

    assert runner.commands == [
        target.openssh_command(
            (
                "btrfs",
                "subvolume",
                "delete",
                tmp_path / "one",
                tmp_path / "two",
            )
        )
    ]


def test_cap_delete_accepts_empty_sequence(tmp_path):
    runner = RecordingRunner()

    with_runner(BtrfsDriver(tmp_path), runner).cap_delete(())

    assert runner.commands == []


def test_cap_delete_rejects_different_endpoint(tmp_path):
    artifact_target = SSHTarget("ssh://artifact", tmp_path / "key")
    driver_target = SSHTarget("ssh://driver", tmp_path / "key")
    artifact = BackupArtifact(
        operation(),
        _snapshot(tmp_path / "snapshot", artifact_target),
    )

    with pytest.raises(BtrfsDriverError, match="different SSH endpoint"):
        BtrfsDriver(tmp_path, driver_target).cap_delete((artifact,))


def test_cap_cleanup_deletes_temporary_snapshot(tmp_path):
    runner = RecordingRunner()
    snapshot = _snapshot(tmp_path / "staging")

    with_runner(BtrfsDriver(tmp_path), runner).cap_cleanup(snapshot)

    assert runner.commands == [
        ("btrfs", "subvolume", "delete", str(snapshot.path)),
    ]


def test_btrfs_send_receive_integration(btrfs_filesystem):
    source = btrfs_filesystem / "source"
    destination = btrfs_filesystem / "destination"
    create = subprocess.run(
        ("btrfs", "subvolume", "create", str(source)),
        capture_output=True,
        check=False,
    )
    if create.returncode != 0:
        pytest.skip("Btrfs subvolumes cannot be created in the test directory")
    destination.mkdir()
    snapshots = []
    artifacts = []
    try:
        (source / "content").write_text("backup content")
        source_driver = BtrfsDriver(source)
        destination_driver = BtrfsDriver(destination)
        snapshot = source_driver.cap_snapshot(source_driver.cap_source())
        snapshots.append(snapshot)
        artifact = destination_driver.cap_import(
            source_driver.cap_export(snapshot),
            operation(),
        )
        artifacts.append(artifact)

        stored = artifact.representation.path
        assert (stored / "content").read_text() == "backup content"
        readonly = subprocess.run(
            ("btrfs", "property", "get", "-ts", str(stored), "ro"),
            capture_output=True,
            check=True,
            text=True,
        )
        assert readonly.stdout.strip() == "ro=true"

        (source / "content").write_text("updated content")
        next_operation = dataclasses.replace(
            operation(),
            created_at=datetime(2026, 8, 27, 13, 30),
        )
        next_snapshot = source_driver.cap_snapshot(source_driver.cap_source())
        snapshots.append(next_snapshot)
        next_artifact = destination_driver.cap_import(
            source_driver.cap_export(next_snapshot, snapshot),
            next_operation,
            artifact.representation,
        )
        artifacts.append(next_artifact)

        assert (next_artifact.representation.path / "content").read_text() == "updated content"
    finally:
        paths = [
            *(artifact.representation.path for artifact in reversed(artifacts)),
            *(snapshot.path for snapshot in reversed(snapshots)),
            source,
        ]
        for path in paths:
            if path is not None and path.exists():
                subprocess.run(
                    ("btrfs", "subvolume", "delete", str(path)),
                    capture_output=True,
                    check=False,
                )


def test_btrfs_representation_types():
    assert issubclass(BtrfsSubvolume, PathTree)
    assert issubclass(BtrfsSnapshot, BtrfsSubvolume)
    assert BtrfsStream.__bases__ == (CommandStream,)
    assert BtrfsStream.suffix == ".btrfs"
