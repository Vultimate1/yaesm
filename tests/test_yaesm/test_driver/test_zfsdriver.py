"""Tests for yaesm.driver.zfsdriver."""

from datetime import datetime

import pytest
import voluptuous as vlp

import yaesm.ty as ty
from yaesm.backup import Backup, BackupArtifact, BackupOperation, DriverSource
from yaesm.command import Command, CommandResult, CommandRunner
from yaesm.driver.zfsdriver import (
    ZFSDataset,
    ZFSDriver,
    ZFSDriverError,
    ZFSSnapshot,
    ZFSStream,
)
from yaesm.errors import YaesmValueError
from yaesm.representation import CommandStream, DataProperty, Representation
from yaesm.ssh import SSHTarget


class RecordingRunner(CommandRunner):
    def __init__(
        self,
        returncodes: ty.Iterable[int] = (),
        stdouts: ty.Iterable[str | None] = (),
        pipeline_failures: ty.Iterable[BaseException | None] = (),
    ) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.checks: list[bool] = []
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
        self.checks.append(check)
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
        normalized = tuple(tuple(str(argument) for argument in command) for command in commands)
        self.pipelines.append(normalized)
        failure = self.pipeline_failures.pop(0) if self.pipeline_failures else None
        if failure is not None:
            raise failure
        return CommandResult(None, "", tuple(0 for _command in normalized))


def with_runner(driver: ZFSDriver, runner: RecordingRunner) -> ZFSDriver:
    driver.runner = runner
    return driver


def operation(hour: int = 12) -> BackupOperation:
    return BackupOperation("example", "hourly", datetime(2026, 8, 27, hour, 30))


def test_name():
    assert ZFSDriver.name() == "zfs"


def test_config_schema_accepts_shorthand():
    assert ZFSDriver.config_schema()("tank/home") == {"dataset": "tank/home"}


def test_config_schema_accepts_expanded_configuration(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")

    assert ZFSDriver.config_schema()({"dataset": "tank/home", "target": target}) == {
        "dataset": "tank/home",
        "target": target,
    }


def test_config_schema_accepts_encryption():
    assert ZFSDriver.config_schema()({"dataset": "tank/home", "encryption": True}) == {
        "dataset": "tank/home",
        "encryption": True,
    }


def test_config_schema_rejects_compression_setting():
    with pytest.raises(vlp.Invalid):
        ZFSDriver.config_schema()({"dataset": "tank/home", "compression": True})


@pytest.mark.parametrize(
    "dataset",
    ["", "/tank/home", "tank/home/", "tank//home", "tank/home@snap", "tank/home#mark", None, 1],
)
def test_config_schema_rejects_invalid_dataset(dataset):
    with pytest.raises(vlp.Invalid, match="dataset must be a ZFS filesystem name"):
        ZFSDriver.config_schema()({"dataset": dataset})


@pytest.mark.parametrize(
    "config",
    [None, [], 1, {}, {"dataset": "tank/home", "unknown": True}],
)
def test_config_schema_rejects_invalid_structure(config):
    with pytest.raises(vlp.Invalid):
        ZFSDriver.config_schema()(config)


def test_config_schema_rejects_invalid_target():
    with pytest.raises(vlp.Invalid, match="target must be an SSHTarget"):
        ZFSDriver.config_schema()({"dataset": "tank/home", "target": "host"})


@pytest.mark.parametrize("encryption", [None, 0, 1, "yes", []])
def test_config_schema_rejects_invalid_encryption(encryption):
    with pytest.raises(vlp.Invalid, match="encryption must be a boolean"):
        ZFSDriver.config_schema()({"dataset": "tank/home", "encryption": encryption})


def test_config_schema_output_constructs_driver():
    config = ZFSDriver.config_schema()("tank/home")

    assert ZFSDriver(**config).dataset == "tank/home"


def test_constructor_rejects_invalid_dataset():
    with pytest.raises(YaesmValueError, match="invalid ZFS dataset"):
        ZFSDriver("tank/home@snapshot")


def test_constructor_rejects_invalid_encryption():
    with pytest.raises(YaesmValueError, match="encryption must be a boolean"):
        ZFSDriver("tank/home", encryption=ty.cast(bool, "yes"))


def test_encryption_is_advertised_by_configured_driver():
    driver = ZFSDriver("tank/home", encryption=True)

    assert driver.capability_metadata("source").adds == {DataProperty.ENCRYPTED}
    assert driver.capability_metadata("store").adds == {DataProperty.ENCRYPTED}
    assert driver.capability_metadata("export").adds == {DataProperty.ENCRYPTED}


def test_encryption_is_not_advertised_by_default():
    driver = ZFSDriver("tank/home")

    assert DataProperty.ENCRYPTED not in driver.capability_metadata("source").adds
    assert DataProperty.ENCRYPTED not in driver.capability_metadata("store").adds
    assert DataProperty.ENCRYPTED not in driver.capability_metadata("export").adds


def test_native_compression_is_not_advertised():
    driver = ZFSDriver("tank/home")

    assert DataProperty.COMPRESSED not in driver.capability_metadata("source").adds
    assert DataProperty.COMPRESSED not in driver.capability_metadata("store").adds
    assert DataProperty.COMPRESSED not in driver.capability_metadata("export").adds


def test_cap_source():
    assert ZFSDriver("tank/home").cap_source() == ZFSDataset("tank/home")


def test_cap_source_includes_ssh_target(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")

    assert ZFSDriver("tank/home", target).cap_source() == ZFSDataset("tank/home", target)


def test_cap_source_verifies_native_encryption():
    runner = RecordingRunner(stdouts=("aes-256-gcm\n",))

    source = with_runner(ZFSDriver("tank/home", encryption=True), runner).cap_source()

    assert source == ZFSDataset("tank/home", encrypted=True)
    assert runner.commands == [("zfs", "get", "-H", "-o", "value", "encryption", "tank/home")]


@pytest.mark.parametrize("value", [None, "", "-\n", "off\n"])
def test_cap_source_rejects_unencrypted_dataset(value):
    runner = RecordingRunner(stdouts=(value,))

    with pytest.raises(ZFSDriverError, match="ZFS dataset is not encrypted: tank/home"):
        with_runner(ZFSDriver("tank/home", encryption=True), runner).cap_source()


def test_cap_source_checks_native_encryption_remotely(tmp_path):
    runner = RecordingRunner(stdouts=("aes-256-gcm\n",))
    target = SSHTarget("ssh://host", tmp_path / "key")

    with_runner(ZFSDriver("tank/home", target, encryption=True), runner).cap_source()

    assert runner.commands == [
        target.openssh_command(("zfs", "get", "-H", "-o", "value", "encryption", "tank/home"))
    ]


def test_cap_snapshot():
    runner = RecordingRunner()

    snapshot = with_runner(ZFSDriver("tank/home"), runner).cap_snapshot(ZFSDataset("tank/home"))

    assert snapshot.dataset == "tank/home"
    assert snapshot.snapshot.startswith(".yaesm-zfs-staging-")
    assert runner.commands == [("zfs", "snapshot", snapshot.name)]


def test_cap_snapshot_remote(tmp_path):
    runner = RecordingRunner()
    target = SSHTarget("ssh://host", tmp_path / "key")

    snapshot = with_runner(ZFSDriver("tank/home"), runner).cap_snapshot(
        ZFSDataset("tank/home", target)
    )

    assert runner.commands == [target.openssh_command(("zfs", "snapshot", snapshot.name))]


def test_cap_snapshot_preserves_encryption():
    snapshot = with_runner(ZFSDriver("tank/home"), RecordingRunner()).cap_snapshot(
        ZFSDataset("tank/home", encrypted=True)
    )

    assert snapshot.encrypted is True


def test_cap_store_snapshots_directly_in_same_dataset():
    runner = RecordingRunner()

    artifact = with_runner(ZFSDriver("tank/home"), runner).cap_store(
        ZFSDataset("tank/home"),
        operation(),
    )

    snapshot = ZFSSnapshot("tank/home", operation().artifact_name)
    assert artifact == BackupArtifact(operation(), snapshot)
    assert runner.commands == [("zfs", "snapshot", snapshot.name)]
    assert runner.pipelines == []


def test_cap_store_snapshots_directly_on_same_remote(tmp_path):
    runner = RecordingRunner()
    source_target = SSHTarget("ssh://host", tmp_path / "source-key")
    destination_target = SSHTarget("ssh://host", tmp_path / "destination-key")

    with_runner(ZFSDriver("tank/home", destination_target), runner).cap_store(
        ZFSDataset("tank/home", source_target),
        operation(),
    )

    snapshot = f"tank/home@{operation().artifact_name}"
    assert runner.commands == [source_target.openssh_command(("zfs", "snapshot", snapshot))]


def test_cap_store_sends_full_snapshot():
    runner = RecordingRunner()

    artifact = with_runner(ZFSDriver("backup/home"), runner).cap_store(
        ZFSDataset("tank/home"),
        operation(),
    )

    source = f"tank/home@{operation().artifact_name}"
    destination = ZFSSnapshot("backup/home", operation().artifact_name)
    assert artifact == BackupArtifact(operation(), destination)
    assert runner.commands == [("zfs", "snapshot", source)]
    assert runner.pipelines == [
        (
            ("zfs", "send", "-c", source),
            (
                "zfs",
                "receive",
                "-u",
                "-o",
                "mountpoint=none",
                "backup/home",
            ),
        )
    ]


def test_cap_store_sends_incremental_and_rotates_source_base():
    runner = RecordingRunner()
    old = operation(11)
    base = ZFSSnapshot("backup/home", old.artifact_name)

    with_runner(ZFSDriver("backup/home"), runner).cap_store(
        ZFSDataset("tank/home"),
        operation(),
        base,
    )

    source_base = f"tank/home@{old.artifact_name}"
    source = f"tank/home@{operation().artifact_name}"
    assert runner.commands == [
        ("zfs", "snapshot", source),
        ("zfs", "destroy", source_base),
    ]
    assert runner.pipelines[0][0] == ("zfs", "send", "-c", "-i", source_base, source)


def test_cap_store_preserves_native_encryption_with_raw_send():
    runner = RecordingRunner()

    artifact = with_runner(ZFSDriver("backup/home"), runner).cap_store(
        ZFSDataset("tank/home", encrypted=True),
        operation(),
    )

    source = f"tank/home@{operation().artifact_name}"
    assert runner.pipelines[0][0] == ("zfs", "send", "-w", source)
    assert artifact.representation.encrypted is True


def test_cap_store_can_enable_native_encryption():
    runner = RecordingRunner(stdouts=("aes-256-gcm\n",))

    with_runner(ZFSDriver("backup/home", encryption=True), runner).cap_store(
        ZFSDataset("tank/home"),
        operation(),
    )

    source = f"tank/home@{operation().artifact_name}"
    assert runner.commands[:2] == [
        ("zfs", "get", "-H", "-o", "value", "encryption", "tank/home"),
        ("zfs", "snapshot", source),
    ]
    assert runner.pipelines[0][0] == ("zfs", "send", "-w", source)


@pytest.mark.parametrize(
    "base",
    [
        ZFSSnapshot("other/home", operation(11).artifact_name),
        ZFSSnapshot(
            "backup/home",
            operation(11).artifact_name,
            SSHTarget("ssh://other", ty.Path("/key")),
        ),
    ],
)
def test_cap_store_rejects_base_outside_destination(base):
    with pytest.raises(ZFSDriverError, match="does not belong to the destination"):
        ZFSDriver("backup/home").cap_store(ZFSDataset("tank/home"), operation(), base)


def test_cap_store_cleans_up_new_source_snapshot_after_failure():
    runner = RecordingRunner(pipeline_failures=(RuntimeError("receive failed"),))

    with pytest.raises(RuntimeError, match="receive failed"):
        with_runner(ZFSDriver("backup/home"), runner).cap_store(
            ZFSDataset("tank/home"),
            operation(),
        )

    source = f"tank/home@{operation().artifact_name}"
    destination = f"backup/home@{operation().artifact_name}"
    assert runner.commands == [
        ("zfs", "snapshot", source),
        ("zfs", "destroy", destination),
        ("zfs", "destroy", source),
    ]
    assert runner.checks == [True, False, False]


def test_cap_export_full():
    snapshot = ZFSSnapshot("tank/home", "snapshot")

    stream = ZFSDriver("tank/home").cap_export(snapshot)

    assert stream == ZFSStream((("zfs", "send", "-c", snapshot.name),))


def test_cap_export_incremental():
    base = ZFSSnapshot("tank/home", "base")
    snapshot = ZFSSnapshot("tank/home", "snapshot")

    stream = ZFSDriver("tank/home").cap_export(snapshot, base)

    assert stream.commands == (("zfs", "send", "-c", "-i", base.name, snapshot.name),)


def test_cap_export_uses_raw_send_for_native_encryption():
    runner = RecordingRunner(stdouts=("aes-256-gcm\n",))
    snapshot = ZFSSnapshot("tank/home", "snapshot")

    stream = with_runner(ZFSDriver("tank/home", encryption=True), runner).cap_export(snapshot)

    assert stream == ZFSStream((("zfs", "send", "-w", snapshot.name),), encrypted=True)
    assert runner.commands == [("zfs", "get", "-H", "-o", "value", "encryption", "tank/home")]


def test_cap_export_uses_raw_incremental_send():
    base = ZFSSnapshot("tank/home", "base", encrypted=True)
    snapshot = ZFSSnapshot("tank/home", "snapshot", encrypted=True)

    stream = ZFSDriver("tank/home").cap_export(snapshot, base)

    assert stream.commands == (("zfs", "send", "-w", "-i", base.name, snapshot.name),)


def test_cap_export_remote(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")
    snapshot = ZFSSnapshot("tank/home", "snapshot", target)

    stream = ZFSDriver("tank/home").cap_export(snapshot)

    assert stream.commands == (target.openssh_command(("zfs", "send", "-c", snapshot.name)),)


@pytest.mark.parametrize(
    "base",
    [
        ZFSSnapshot("other/home", "base"),
        ZFSSnapshot("tank/home", "base", SSHTarget("ssh://other", ty.Path("/key"))),
    ],
)
def test_cap_export_rejects_incompatible_base(base):
    with pytest.raises(ZFSDriverError, match="different datasets"):
        ZFSDriver("tank/home").cap_export(ZFSSnapshot("tank/home", "snapshot"), base)


def test_cap_import_remote(tmp_path):
    runner = RecordingRunner()
    target = SSHTarget("ssh://host", tmp_path / "key")
    stream = ZFSStream((("zfs", "send", "tank/home@snapshot"),))

    artifact = with_runner(ZFSDriver("backup/home", target), runner).cap_import(stream, operation())

    assert artifact == BackupArtifact(
        operation(),
        ZFSSnapshot("backup/home", operation().artifact_name, target),
    )
    assert runner.pipelines == [
        (
            stream.commands[0],
            target.openssh_command(
                (
                    "zfs",
                    "receive",
                    "-u",
                    "-o",
                    "mountpoint=none",
                    "backup/home",
                )
            ),
        )
    ]


def test_cap_import_rejects_incompatible_base():
    stream = ZFSStream((("zfs", "send", "tank/home@snapshot"),))

    with pytest.raises(ZFSDriverError, match="does not belong to the destination"):
        ZFSDriver("backup/home").cap_import(
            stream,
            operation(),
            ZFSSnapshot("other/home", "base"),
        )


def test_cap_import_cleans_up_failed_snapshot():
    runner = RecordingRunner(pipeline_failures=(RuntimeError("receive failed"),))
    stream = ZFSStream((("zfs", "send", "tank/home@snapshot"),))

    with pytest.raises(RuntimeError, match="receive failed"):
        with_runner(ZFSDriver("backup/home"), runner).cap_import(stream, operation())

    assert runner.commands == [("zfs", "destroy", f"backup/home@{operation().artifact_name}")]
    assert runner.checks == [False]


def test_cap_import_preserves_encryption_state():
    runner = RecordingRunner()
    stream = ZFSStream(
        (("zfs", "send", "-w", "tank/home@snapshot"),),
        encrypted=True,
    )

    artifact = with_runner(ZFSDriver("backup/home"), runner).cap_import(stream, operation())

    assert artifact.representation.encrypted is True


def test_cap_list_returns_matching_artifacts_newest_first():
    older = operation(11)
    newer = operation(12)
    runner = RecordingRunner(
        stdouts=(
            "\n".join(
                (
                    f"backup/home@{older.artifact_name}",
                    "backup/home@not-an-artifact",
                    f"other/home@{newer.artifact_name}",
                    f"backup/home@{newer.artifact_name}",
                )
            ),
        )
    )

    artifacts = with_runner(ZFSDriver("backup/home"), runner).cap_list("example")

    assert artifacts == (
        BackupArtifact(newer, ZFSSnapshot("backup/home", newer.artifact_name)),
        BackupArtifact(older, ZFSSnapshot("backup/home", older.artifact_name)),
    )
    assert runner.commands == [
        (
            "zfs",
            "list",
            "-H",
            "-t",
            "snapshot",
            "-o",
            "name",
            "-d",
            "1",
            "backup/home",
        )
    ]
    assert runner.checks == [False]


def test_cap_list_returns_empty_when_dataset_does_not_exist():
    runner = RecordingRunner((1,))

    assert with_runner(ZFSDriver("backup/home"), runner).cap_list("example") == ()


def test_cap_list_remote(tmp_path):
    runner = RecordingRunner()
    target = SSHTarget("ssh://host", tmp_path / "key")

    assert with_runner(ZFSDriver("backup/home", target), runner).cap_list("example") == ()
    assert runner.commands == [
        target.openssh_command(
            (
                "zfs",
                "list",
                "-H",
                "-t",
                "snapshot",
                "-o",
                "name",
                "-d",
                "1",
                "backup/home",
            )
        )
    ]


def test_cap_delete_batches_snapshots():
    runner = RecordingRunner()
    artifacts = (
        BackupArtifact(operation(11), ZFSSnapshot("backup/home", "one")),
        BackupArtifact(operation(12), ZFSSnapshot("backup/home", "two")),
    )

    with_runner(ZFSDriver("backup/home"), runner).cap_delete(artifacts)

    assert runner.commands == [("zfs", "destroy", "backup/home@one,two")]


def test_cap_delete_batches_remote_snapshots(tmp_path):
    runner = RecordingRunner()
    target = SSHTarget("ssh://host", tmp_path / "key")
    artifacts = (
        BackupArtifact(operation(11), ZFSSnapshot("backup/home", "one", target)),
        BackupArtifact(operation(12), ZFSSnapshot("backup/home", "two", target)),
    )

    with_runner(ZFSDriver("backup/home", target), runner).cap_delete(artifacts)

    assert runner.commands == [target.openssh_command(("zfs", "destroy", "backup/home@one,two"))]


def test_cap_delete_accepts_empty_sequence():
    runner = RecordingRunner()

    with_runner(ZFSDriver("backup/home"), runner).cap_delete(())

    assert runner.commands == []


@pytest.mark.parametrize(
    "snapshot",
    [
        ZFSSnapshot("other/home", "one"),
        ZFSSnapshot("backup/home", "one", SSHTarget("ssh://other", ty.Path("/key"))),
    ],
)
def test_cap_delete_rejects_snapshot_outside_destination(snapshot):
    artifact = BackupArtifact(operation(), snapshot)

    with pytest.raises(ZFSDriverError, match="does not belong to the destination"):
        ZFSDriver("backup/home").cap_delete((artifact,))


def test_cap_cleanup_destroys_snapshot():
    runner = RecordingRunner()

    with_runner(ZFSDriver("tank/home"), runner).cap_cleanup(ZFSSnapshot("tank/home", "staging"))

    assert runner.commands == [("zfs", "destroy", "tank/home@staging")]


def test_backup_execute_uses_zfs_store_and_incremental_base():
    old = operation(11)
    runner = RecordingRunner(stdouts=(f"backup/home@{old.artifact_name}\n",))
    backup = Backup(
        "example",
        DriverSource(ZFSDriver("tank/home")),
        with_runner(ZFSDriver("backup/home"), runner),
    )

    artifact = backup.execute("hourly", operation().created_at)

    assert artifact == BackupArtifact(
        operation(),
        ZFSSnapshot("backup/home", operation().artifact_name),
    )
    assert runner.pipelines[0][0] == (
        "zfs",
        "send",
        "-c",
        "-i",
        f"tank/home@{old.artifact_name}",
        f"tank/home@{operation().artifact_name}",
    )


@pytest.mark.parametrize("requirements", [frozenset(), frozenset({DataProperty.ENCRYPTED})])
def test_backup_execute_preserves_configured_native_encryption(requirements):
    source_runner = RecordingRunner(stdouts=("aes-256-gcm\n",))
    destination_runner = RecordingRunner()
    backup = Backup(
        "example",
        DriverSource(with_runner(ZFSDriver("tank/home", encryption=True), source_runner)),
        with_runner(ZFSDriver("backup/home"), destination_runner),
        requirements=requirements,
    )

    artifact = backup.execute("hourly", operation().created_at)

    source = f"tank/home@{operation().artifact_name}"
    assert destination_runner.pipelines[0][0] == ("zfs", "send", "-w", source)
    assert artifact.representation.encrypted is True


def test_zfs_representation_types():
    assert isinstance(ZFSDataset("tank/home"), Representation)
    assert isinstance(ZFSSnapshot("tank/home", "snapshot"), Representation)
    assert ZFSStream.__bases__ == (CommandStream,)
