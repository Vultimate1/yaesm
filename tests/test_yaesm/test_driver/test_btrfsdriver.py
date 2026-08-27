"""Tests for yaesm.driver.btrfsdriver."""

import shutil
import subprocess
from datetime import datetime

import pytest
import voluptuous as vlp

import yaesm.ty as ty
from yaesm.backup import BackupArtifact, BackupOperation
from yaesm.command import Command, CommandResult, CommandRunner
from yaesm.driver.btrfsdriver import (
    BtrfsDriver,
    BtrfsDriverError,
    BtrfsSnapshot,
    BtrfsStream,
    BtrfsSubvolume,
)
from yaesm.representation import ByteStream, PathTree
from yaesm.ssh import SSHTarget, command_for_target


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
        self.commands.append(tuple(str(argument) for argument in command))
        returncode = self.returncodes.pop(0) if self.returncodes else 0
        stdout = self.stdouts.pop(0) if self.stdouts else None
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


def operation() -> BackupOperation:
    return BackupOperation("example", "manual", datetime(2026, 8, 27, 12, 30))


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
        "delete",
    }
    assert driver.cap_source() == BtrfsSubvolume(tmp_path)


def test_bootstrap_refresh_can_be_disabled(tmp_path):
    assert BtrfsDriver(tmp_path, bootstrap_refresh_days=0).bootstrap_refresh_days is None


def test_negative_bootstrap_refresh_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="must be at least 0, got -1"):
        BtrfsDriver(tmp_path, bootstrap_refresh_days=-1)


def test_cap_source_includes_ssh_target(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")

    assert BtrfsDriver(tmp_path, target).cap_source() == BtrfsSubvolume(tmp_path, target)


def test_cap_snapshot(tmp_path):
    runner = RecordingRunner()
    driver = BtrfsDriver(tmp_path, runner=runner)
    source = driver.cap_source()

    snapshot = driver.cap_snapshot(source)

    assert snapshot.path.parent == source.path
    assert snapshot.path.name.startswith(".yaesm-btrfs-staging-")
    assert snapshot.target is None
    assert runner.commands == [
        ("btrfs", "subvolume", "snapshot", "-r", str(source.path), str(snapshot.path))
    ]


def test_cap_snapshot_remote(tmp_path):
    runner = RecordingRunner()
    target = SSHTarget("ssh://host", tmp_path / "key")
    driver = BtrfsDriver(tmp_path, target, runner=runner)

    snapshot = driver.cap_snapshot(driver.cap_source())

    remote_command = ("btrfs", "subvolume", "snapshot", "-r", tmp_path, snapshot.path)
    assert snapshot.target is target
    assert runner.commands == [target.openssh_command(remote_command)]


def test_cap_store_uses_direct_snapshot_on_same_endpoint(tmp_path):
    runner = RecordingRunner()
    source = BtrfsSubvolume(tmp_path / "source")
    driver = BtrfsDriver(tmp_path / "destination", runner=runner)

    artifact = driver.cap_store(source, operation())

    destination = tmp_path / "destination" / operation().artifact_name
    assert artifact == BackupArtifact(operation(), BtrfsSnapshot(destination))
    assert runner.commands == [
        (
            "btrfs",
            "subvolume",
            "snapshot",
            "-r",
            str(source.path),
            str(destination),
        )
    ]
    assert runner.pipelines == []


def test_cap_store_falls_back_to_send_receive(tmp_path):
    runner = RecordingRunner((1, 1, 1))
    source = BtrfsSubvolume(tmp_path / "source")
    destination_dir = tmp_path / "destination"
    driver = BtrfsDriver(destination_dir, runner=runner)

    artifact = driver.cap_store(source, operation())

    bootstrap = source.path / ".yaesm-btrfs-bootstrap-example"
    staging = ty.Path(runner.commands[4][-1])
    destination = destination_dir / operation().artifact_name
    assert artifact == BackupArtifact(operation(), BtrfsSnapshot(destination))
    assert runner.pipelines == [
        (
            ("btrfs", "send", str(bootstrap)),
            ("btrfs", "receive", str(destination_dir)),
        ),
        (
            ("btrfs", "send", "-p", str(bootstrap), str(staging)),
            ("btrfs", "receive", str(destination_dir)),
        ),
    ]
    assert runner.commands[5:] == [
        ("mv", "--", str(destination_dir / staging.name), str(destination)),
        ("btrfs", "subvolume", "delete", str(staging)),
    ]


def test_cap_store_uses_explicit_base_without_bootstrap(tmp_path):
    runner = RecordingRunner((1,))
    source = BtrfsSubvolume(tmp_path / "source")
    base = BtrfsSnapshot(source.path / "base")
    destination_dir = tmp_path / "destination"

    BtrfsDriver(destination_dir, runner=runner).cap_store(source, operation(), base)

    staging = ty.Path(runner.commands[1][-1])
    assert runner.pipelines == [
        (
            ("btrfs", "send", "-p", str(base.path), str(staging)),
            ("btrfs", "receive", str(destination_dir)),
        )
    ]
    assert all(command[:3] != ("btrfs", "subvolume", "show") for command in runner.commands)


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

    BtrfsDriver(destination_dir, destination_target, runner=runner).cap_store(
        source,
        operation(),
    )

    bootstrap = source.path / ".yaesm-btrfs-bootstrap-example"
    receive = command_for_target(
        destination_target,
        ("btrfs", "receive", destination_dir),
    )
    assert runner.pipelines[0] == (
        command_for_target(source_target, ("btrfs", "send", bootstrap)),
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

    artifact = BtrfsDriver(destination_dir, destination_target, runner=runner).cap_store(
        source,
        operation(),
    )

    destination = destination_dir / operation().artifact_name
    assert artifact == BackupArtifact(
        operation(),
        BtrfsSnapshot(destination, destination_target),
    )
    assert runner.commands == [
        destination_target.openssh_command(
            ("btrfs", "subvolume", "snapshot", "-r", source.path, destination)
        )
    ]
    assert runner.pipelines == []


def test_cap_store_reuses_bootstrap(tmp_path):
    runner = RecordingRunner((1, 0, 0), (None, None, None, ""))
    source = BtrfsSubvolume(tmp_path / "source")
    destination_dir = tmp_path / "destination"
    driver = BtrfsDriver(
        destination_dir,
        bootstrap_refresh_days=21,
        runner=runner,
    )

    driver.cap_store(source, operation())

    bootstrap = source.path / ".yaesm-btrfs-bootstrap-example"
    staging = ty.Path(runner.commands[4][-1])
    assert runner.pipelines == [
        (
            ("btrfs", "send", "-p", str(bootstrap), str(staging)),
            ("btrfs", "receive", str(destination_dir)),
        )
    ]


def test_cap_store_refreshes_stale_bootstrap(tmp_path):
    source = BtrfsSubvolume(tmp_path / "source")
    destination_dir = tmp_path / "destination"
    source_bootstrap = source.path / ".yaesm-btrfs-bootstrap-example"
    destination_bootstrap = destination_dir / source_bootstrap.name
    runner = RecordingRunner(
        (1, 0, 0),
        (None, None, None, f"{source_bootstrap}\n"),
    )
    driver = BtrfsDriver(
        destination_dir,
        bootstrap_refresh_days=21,
        runner=runner,
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
        ("btrfs", "send", str(source_bootstrap)),
        ("btrfs", "receive", str(destination_dir)),
    )
    assert runner.pipelines[1][0][0:4] == (
        "btrfs",
        "send",
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
    driver = BtrfsDriver(destination_dir, runner=runner)

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

    BtrfsDriver(destination_dir, runner=runner).cap_store(source, operation())

    assert runner.commands[3] == (
        "btrfs",
        "subvolume",
        "delete",
        str(destination_bootstrap),
    )
    assert runner.pipelines[0][0] == (
        "btrfs",
        "send",
        str(source.path / destination_bootstrap.name),
    )


def test_cap_store_with_disabled_bootstrap_refresh(tmp_path):
    source = BtrfsSubvolume(tmp_path / "source")
    destination_dir = tmp_path / "destination"
    bootstrap = source.path / ".yaesm-btrfs-bootstrap-example"
    runner = RecordingRunner((1, 0, 0))

    BtrfsDriver(
        destination_dir,
        bootstrap_refresh_days=0,
        runner=runner,
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
        BtrfsDriver(destination_dir, runner=runner).cap_store(source, operation())

    assert runner.commands[-1] == (
        "btrfs",
        "subvolume",
        "delete",
        str(destination_bootstrap),
    )


def test_cap_export_full(tmp_path):
    snapshot = BtrfsSnapshot(tmp_path / "snapshot")

    stream = BtrfsDriver(tmp_path).cap_export(snapshot)

    assert stream.commands == (("btrfs", "send", str(snapshot.path)),)
    assert stream.subvolume_name == "snapshot"


def test_cap_export_incremental(tmp_path):
    base = BtrfsSnapshot(tmp_path / "base")
    snapshot = BtrfsSnapshot(tmp_path / "snapshot")

    stream = BtrfsDriver(tmp_path).cap_export(snapshot, base)

    assert stream.commands == (("btrfs", "send", "-p", str(base.path), str(snapshot.path)),)


def test_cap_export_remote(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")
    snapshot = BtrfsSnapshot(tmp_path / "snapshot", target)

    stream = BtrfsDriver(tmp_path).cap_export(snapshot)

    assert stream.commands == (target.openssh_command(("btrfs", "send", snapshot.path)),)


def test_cap_export_rejects_base_on_different_endpoint(tmp_path):
    source_target = SSHTarget("ssh://source", tmp_path / "key")
    base_target = SSHTarget("ssh://base", tmp_path / "key")
    snapshot = BtrfsSnapshot(tmp_path / "snapshot", source_target)
    base = BtrfsSnapshot(tmp_path / "base", base_target)

    with pytest.raises(BtrfsDriverError, match="different SSH endpoints"):
        BtrfsDriver(tmp_path).cap_export(snapshot, base)


def test_cap_import_rejects_base_on_different_endpoint(tmp_path):
    destination_target = SSHTarget("ssh://destination", tmp_path / "key")
    base_target = SSHTarget("ssh://base", tmp_path / "key")
    stream = BtrfsStream(
        (("btrfs", "send", str(tmp_path / "snapshot")),),
        subvolume_name="snapshot",
    )
    base = BtrfsSnapshot(tmp_path / "base", base_target)

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
        BtrfsDriver(tmp_path, runner=runner).cap_import(stream, operation())

    assert runner.commands == [("btrfs", "subvolume", "delete", str(tmp_path / "snapshot"))]


def test_cap_delete_batches_artifacts(tmp_path):
    runner = RecordingRunner()
    artifacts = (
        BackupArtifact(operation(), BtrfsSnapshot(tmp_path / "one")),
        BackupArtifact(operation(), BtrfsSnapshot(tmp_path / "two")),
    )

    BtrfsDriver(tmp_path, runner=runner).cap_delete(artifacts)

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
        BackupArtifact(operation(), BtrfsSnapshot(tmp_path / "one", target)),
        BackupArtifact(operation(), BtrfsSnapshot(tmp_path / "two", target)),
    )

    BtrfsDriver(tmp_path, target, runner=runner).cap_delete(artifacts)

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

    BtrfsDriver(tmp_path, runner=runner).cap_delete(())

    assert runner.commands == []


def test_cap_delete_rejects_different_endpoint(tmp_path):
    artifact_target = SSHTarget("ssh://artifact", tmp_path / "key")
    driver_target = SSHTarget("ssh://driver", tmp_path / "key")
    artifact = BackupArtifact(
        operation(),
        BtrfsSnapshot(tmp_path / "snapshot", artifact_target),
    )

    with pytest.raises(BtrfsDriverError, match="different SSH endpoint"):
        BtrfsDriver(tmp_path, driver_target).cap_delete((artifact,))


def test_btrfs_send_receive_integration(tmp_path):
    if shutil.which("btrfs") is None:
        pytest.skip("btrfs is not installed")
    filesystem = subprocess.run(
        ("btrfs", "filesystem", "usage", str(tmp_path)),
        capture_output=True,
        check=False,
    )
    if filesystem.returncode != 0:
        pytest.skip("test directory is not on a usable Btrfs filesystem")

    source = tmp_path / "source"
    destination = tmp_path / "destination"
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
    assert issubclass(BtrfsStream, ByteStream)
