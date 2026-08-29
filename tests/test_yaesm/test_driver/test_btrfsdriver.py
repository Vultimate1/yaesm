"""Tests for yaesm.driver.btrfsdriver."""

import subprocess
from datetime import datetime
from uuid import UUID

import pytest
import voluptuous as vlp

import yaesm.command as command_module
import yaesm.ty as ty
from yaesm.backup import Backup, BackupArtifact, BackupOperation
from yaesm.check import CheckRole
from yaesm.command import Command, CommandResult, CommandRunner
from yaesm.driver.btrfsdriver import (
    BtrfsDriver,
    BtrfsDriverError,
    BtrfsSnapshot,
    BtrfsStream,
    BtrfsSubvolume,
)
from yaesm.errors import YaesmValueError
from yaesm.representation import CommandStream, DataProperty, PathTree
from yaesm.ssh import SSHTarget, command_for_target

_BTRFS_SEND = ("btrfs", "send", "--compressed-data")
_SNAPSHOT_UUID = UUID("11111111-1111-1111-1111-111111111111")


def _snapshot(
    path: ty.Path,
    target: SSHTarget | None = None,
    *,
    uuid: UUID = _SNAPSHOT_UUID,
    source_uuid: UUID | None = None,
) -> BtrfsSnapshot:
    return BtrfsSnapshot(path, target, uuid=uuid, source_uuid=source_uuid)


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
        commands: ty.Sequence[Command],
        *,
        capture_output: bool = False,
        check: bool = True,
    ) -> CommandResult:
        pipeline = tuple(tuple(str(argument) for argument in command) for command in commands)
        self.pipelines.append(pipeline)
        if self.pipeline_failures:
            failure = self.pipeline_failures.pop(0)
            if failure is not None:
                raise failure
        return CommandResult(None, "", (0,) * len(pipeline))


def with_runner(driver: BtrfsDriver, runner: RecordingRunner) -> BtrfsDriver:
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
    assert BtrfsDriver.config_schema()({"location": str(tmp_path)}) == {
        "location": tmp_path,
        "bootstrap_refresh_days": 21,
    }


def test_config_schema_accepts_path_location(tmp_path):
    assert BtrfsDriver.config_schema()({"location": tmp_path})["location"] == tmp_path


def test_config_schema_accepts_ssh_target(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")

    assert BtrfsDriver.config_schema()({"location": str(tmp_path), "target": target}) == {
        "location": tmp_path,
        "target": target,
        "bootstrap_refresh_days": 21,
    }


@pytest.mark.parametrize("refresh_days", [0, 1, 21])
def test_config_schema_accepts_bootstrap_refresh(tmp_path, refresh_days):
    assert BtrfsDriver.config_schema()(
        {"location": str(tmp_path), "bootstrap_refresh_days": refresh_days}
    ) == {
        "location": tmp_path,
        "bootstrap_refresh_days": refresh_days,
    }


@pytest.mark.parametrize("location", [None, 42])
def test_config_schema_rejects_invalid_location_type(location):
    with pytest.raises(vlp.Invalid, match="location must be a path"):
        BtrfsDriver.config_schema()({"location": location})


@pytest.mark.parametrize("refresh_days", [True, False])
def test_config_schema_rejects_boolean_bootstrap_refresh(refresh_days):
    with pytest.raises(vlp.Invalid, match="must be an integer"):
        BtrfsDriver.config_schema()({"location": "/tmp", "bootstrap_refresh_days": refresh_days})


@pytest.mark.parametrize("refresh_days", ["21", 21.0, None])
def test_config_schema_rejects_invalid_bootstrap_refresh_type(refresh_days):
    with pytest.raises(vlp.Invalid, match="must be an integer"):
        BtrfsDriver.config_schema()({"location": "/tmp", "bootstrap_refresh_days": refresh_days})


def test_config_schema_rejects_explicit_none_target():
    with pytest.raises(vlp.Invalid, match="target must be an SSHTarget"):
        BtrfsDriver.config_schema()({"location": "/tmp", "target": None})


def test_config_schema_output_constructs_driver(tmp_path):
    config = BtrfsDriver.config_schema()({"location": tmp_path, "bootstrap_refresh_days": 0})

    driver = BtrfsDriver(**config)

    assert driver.location == tmp_path
    assert driver.target is None
    assert driver.bootstrap_refresh_days is None


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"location": "relative"},
        {"location": "/tmp", "target": "ssh://host"},
        {"location": "/tmp", "bootstrap_refresh_days": -1},
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


def test_bootstrap_refresh_can_be_disabled(tmp_path):
    assert BtrfsDriver(tmp_path, bootstrap_refresh_days=0).bootstrap_refresh_days is None


def test_negative_bootstrap_refresh_is_rejected(tmp_path):
    with pytest.raises(YaesmValueError, match="must be at least 0, got -1"):
        BtrfsDriver(tmp_path, bootstrap_refresh_days=-1)


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
    assert snapshot.target is None
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
    assert snapshot.target is target
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
    runner = RecordingRunner((1, 1, 1))
    source = BtrfsSubvolume(tmp_path / "source")
    destination_dir = tmp_path / "destination"
    driver = with_runner(BtrfsDriver(destination_dir), runner)

    artifact = driver.cap_store(source, operation())

    bootstrap = source.path / ".yaesm-btrfs-bootstrap-example"
    staging = ty.Path(runner.commands[5][-1])
    destination = destination_dir / operation().artifact_name
    assert artifact == BackupArtifact(operation(), _snapshot(destination))
    assert runner.pipelines == [
        (
            (*_BTRFS_SEND, str(bootstrap)),
            ("btrfs", "receive", str(destination_dir)),
        ),
        (
            (*_BTRFS_SEND, "-p", str(bootstrap), str(staging)),
            ("btrfs", "receive", str(destination_dir)),
        ),
    ]
    assert runner.commands[7:] == [
        ("mv", "-T", "--", str(destination_dir / staging.name), str(destination)),
        ("btrfs", "subvolume", "show", str(destination)),
        ("btrfs", "subvolume", "delete", str(staging)),
    ]


def test_backup_execute_uses_readonly_snapshot_with_incremental_send_fallback(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    runner = RecordingRunner((0, 1, 1, 1))
    backup = Backup(
        "example",
        BtrfsDriver(source),
        with_runner(BtrfsDriver(destination), runner),
    )

    artifact = backup.execute("manual", operation().created_at)

    bootstrap = source / ".yaesm-btrfs-bootstrap-example"
    staging = ty.Path(runner.commands[6][-1])
    stored = destination / operation().artifact_name
    assert artifact == BackupArtifact(operation(), _snapshot(stored))
    assert runner.commands[1] == (
        "btrfs",
        "subvolume",
        "snapshot",
        "-r",
        str(source),
        str(stored),
    )
    assert runner.commands[4] == (
        "btrfs",
        "subvolume",
        "snapshot",
        "-r",
        str(source),
        str(bootstrap),
    )
    assert runner.commands[6] == (
        "btrfs",
        "subvolume",
        "snapshot",
        "-r",
        str(source),
        str(staging),
    )
    assert runner.pipelines[-1] == (
        (*_BTRFS_SEND, "-p", str(bootstrap), str(staging)),
        ("btrfs", "receive", str(destination)),
    )


def test_cap_store_uses_explicit_base_without_bootstrap(tmp_path):
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
    assert all(".yaesm-btrfs-bootstrap" not in " ".join(command) for command in runner.commands)


@pytest.mark.parametrize(
    ("source_spec", "destination_spec"),
    [
        (None, "ssh://destination"),
        ("ssh://source", None),
        ("ssh://source", "ssh://destination"),
    ],
)
def test_cap_store_bootstraps_between_different_endpoints(
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
    runner = RecordingRunner((1, 1))

    with_runner(BtrfsDriver(destination_dir, destination_target), runner).cap_store(
        source,
        operation(),
    )

    bootstrap = source.path / ".yaesm-btrfs-bootstrap-example"
    receive = command_for_target(
        destination_target,
        ("btrfs", "receive", destination_dir),
    )
    assert runner.pipelines[0] == (
        command_for_target(source_target, (*_BTRFS_SEND, bootstrap)),
        receive,
    )
    assert runner.pipelines[1][1] == receive
    assert "-p" in runner.pipelines[1][0] or " -p " in runner.pipelines[1][0][-1]
    assert str(bootstrap) in " ".join(runner.pipelines[1][0])


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


def test_cap_store_reuses_bootstrap(tmp_path):
    runner = RecordingRunner((1, 0, 0), (None, None, None, ""))
    source = BtrfsSubvolume(tmp_path / "source")
    destination_dir = tmp_path / "destination"
    driver = with_runner(
        BtrfsDriver(destination_dir, bootstrap_refresh_days=21),
        runner,
    )

    driver.cap_store(source, operation(previous_backup_names=("old-example",)))

    bootstrap = source.path / ".yaesm-btrfs-bootstrap-example"
    staging = ty.Path(runner.commands[4][-1])
    assert runner.pipelines == [
        (
            (*_BTRFS_SEND, "-p", str(bootstrap), str(staging)),
            ("btrfs", "receive", str(destination_dir)),
        )
    ]
    assert all("old-example" not in " ".join(command) for command in runner.commands)


@pytest.mark.parametrize(
    ("previous_names", "returncodes"),
    [
        (("old-example",), (1, 1, 1, 0, 0)),
        (("missing", "old-example"), (1, 1, 1, 1, 1, 0, 0)),
    ],
)
def test_cap_store_reuses_bootstrap_under_previous_backup_name(
    tmp_path,
    previous_names,
    returncodes,
):
    runner = RecordingRunner(returncodes)
    source = BtrfsSubvolume(tmp_path / "source")
    destination = tmp_path / "destination"
    driver = with_runner(BtrfsDriver(destination), runner)
    driver.cap_store(source, operation(previous_backup_names=previous_names))

    bootstrap = source.path / ".yaesm-btrfs-bootstrap-old-example"
    assert runner.pipelines[0][0][: len(_BTRFS_SEND) + 2] == (
        *_BTRFS_SEND,
        "-p",
        str(bootstrap),
    )


def test_cap_store_refreshes_stale_bootstrap(tmp_path):
    source = BtrfsSubvolume(tmp_path / "source")
    destination_dir = tmp_path / "destination"
    source_bootstrap = source.path / ".yaesm-btrfs-bootstrap-example"
    destination_bootstrap = destination_dir / source_bootstrap.name
    runner = RecordingRunner(
        (1, 0, 0),
        (None, None, None, f"{source_bootstrap}\n"),
    )
    driver = with_runner(
        BtrfsDriver(destination_dir, bootstrap_refresh_days=21),
        runner,
    )

    driver.cap_store(source, operation())

    assert runner.commands[3:7] == [
        ("find", str(source_bootstrap), "-prune", "-mtime", "+20", "-print"),
        ("btrfs", "subvolume", "delete", str(source_bootstrap)),
        ("btrfs", "subvolume", "delete", str(destination_bootstrap)),
        (
            "btrfs",
            "subvolume",
            "snapshot",
            "-r",
            str(source.path),
            str(source_bootstrap),
        ),
    ]
    assert runner.pipelines[0] == (
        (*_BTRFS_SEND, str(source_bootstrap)),
        ("btrfs", "receive", str(destination_dir)),
    )
    assert runner.pipelines[1][0][: len(_BTRFS_SEND) + 2] == (
        *_BTRFS_SEND,
        "-p",
        str(source_bootstrap),
    )


def test_cap_store_refreshes_stale_bootstrap_without_destination_copy(tmp_path):
    source = BtrfsSubvolume(tmp_path / "source")
    destination_dir = tmp_path / "destination"
    source_bootstrap = source.path / ".yaesm-btrfs-bootstrap-example"
    destination_bootstrap = destination_dir / source_bootstrap.name
    runner = RecordingRunner(
        (1, 0, 1),
        (None, None, None, f"{source_bootstrap}\n"),
    )
    driver = with_runner(BtrfsDriver(destination_dir), runner)

    driver.cap_store(source, operation())

    delete_commands = [
        command for command in runner.commands if command[:3] == ("btrfs", "subvolume", "delete")
    ]
    assert ("btrfs", "subvolume", "delete", str(source_bootstrap)) in delete_commands
    assert ("btrfs", "subvolume", "delete", str(destination_bootstrap)) not in delete_commands


def test_cap_store_repairs_orphaned_destination_bootstrap(tmp_path):
    source = BtrfsSubvolume(tmp_path / "source")
    destination_dir = tmp_path / "destination"
    destination_bootstrap = destination_dir / ".yaesm-btrfs-bootstrap-example"
    runner = RecordingRunner((1, 1, 0))

    with_runner(BtrfsDriver(destination_dir), runner).cap_store(source, operation())

    assert runner.commands[3] == (
        "btrfs",
        "subvolume",
        "delete",
        str(destination_bootstrap),
    )
    assert runner.pipelines[0][0] == (
        *_BTRFS_SEND,
        str(source.path / destination_bootstrap.name),
    )


def test_cap_store_with_disabled_bootstrap_refresh(tmp_path):
    source = BtrfsSubvolume(tmp_path / "source")
    destination_dir = tmp_path / "destination"
    bootstrap = source.path / ".yaesm-btrfs-bootstrap-example"
    runner = RecordingRunner((1, 0, 0))

    with_runner(
        BtrfsDriver(destination_dir, bootstrap_refresh_days=0),
        runner,
    ).cap_store(source, operation())

    assert all(command[0] != "find" for command in runner.commands)
    assert str(bootstrap) in runner.pipelines[0][0]
    assert "-p" in runner.pipelines[0][0]


def test_cap_store_cleans_up_failed_bootstrap_receive(tmp_path):
    source = BtrfsSubvolume(tmp_path / "source")
    destination_dir = tmp_path / "destination"
    destination_bootstrap = destination_dir / ".yaesm-btrfs-bootstrap-example"
    runner = RecordingRunner(
        (1, 1, 1),
        pipeline_failures=(RuntimeError("receive failed"),),
    )

    with pytest.raises(RuntimeError, match="receive failed"):
        with_runner(BtrfsDriver(destination_dir), runner).cap_store(source, operation())

    assert runner.commands[-1] == (
        "btrfs",
        "subvolume",
        "delete",
        str(destination_bootstrap),
    )


def test_cap_export_full(tmp_path):
    snapshot = _snapshot(tmp_path / "snapshot")

    stream = BtrfsDriver(tmp_path).cap_export(snapshot)

    assert stream.commands == ((*_BTRFS_SEND, str(snapshot.path)),)
    assert stream.subvolume_name == "snapshot"
    assert stream.suffixes == (".btrfs",)


def test_cap_export_incremental(tmp_path):
    base = _snapshot(tmp_path / "base")
    snapshot = _snapshot(tmp_path / "snapshot")

    stream = BtrfsDriver(tmp_path).cap_export(snapshot, base)

    assert stream.commands == ((*_BTRFS_SEND, "-p", str(base.path), str(snapshot.path)),)


def test_cap_export_remote(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")
    snapshot = _snapshot(tmp_path / "snapshot", target)

    stream = BtrfsDriver(tmp_path).cap_export(snapshot)

    assert stream.commands == (target.openssh_command((*_BTRFS_SEND, snapshot.path)),)


def test_cap_export_rejects_base_on_different_endpoint(tmp_path):
    source_target = SSHTarget("ssh://source", tmp_path / "key")
    base_target = SSHTarget("ssh://base", tmp_path / "key")
    snapshot = _snapshot(tmp_path / "snapshot", source_target)
    base = _snapshot(tmp_path / "base", base_target)

    with pytest.raises(BtrfsDriverError, match="different SSH endpoints"):
        BtrfsDriver(tmp_path).cap_export(snapshot, base)


def test_cap_import_rejects_base_on_different_endpoint(tmp_path):
    destination_target = SSHTarget("ssh://destination", tmp_path / "key")
    base_target = SSHTarget("ssh://base", tmp_path / "key")
    stream = BtrfsStream(
        (("btrfs", "send", str(tmp_path / "snapshot")),),
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
        (("btrfs", "send", str(tmp_path / "snapshot")),),
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
        (("btrfs", "send", str(tmp_path / "snapshot")),),
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
    snapshot = None
    artifact = None
    try:
        (source / "content").write_text("backup content")
        source_driver = BtrfsDriver(source)
        snapshot = source_driver.cap_snapshot(source_driver.cap_source())
        artifact = BtrfsDriver(destination).cap_import(
            source_driver.cap_export(snapshot),
            operation(),
        )

        stored = artifact.representation.path
        assert (stored / "content").read_text() == "backup content"
        readonly = subprocess.run(
            ("btrfs", "property", "get", "-ts", str(stored), "ro"),
            capture_output=True,
            check=True,
            text=True,
        )
        assert readonly.stdout.strip() == "ro=true"
    finally:
        paths = [
            None if artifact is None else artifact.representation.path,
            None if snapshot is None else snapshot.path,
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
