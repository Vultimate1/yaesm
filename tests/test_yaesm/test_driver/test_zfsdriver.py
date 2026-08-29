"""Tests for yaesm.driver.zfsdriver."""

import dataclasses
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
import voluptuous as vlp

import yaesm.command as command_module
import yaesm.ty as ty
from yaesm.backup import Backup, BackupArtifact, BackupError, BackupOperation
from yaesm.check import CheckRole
from yaesm.command import (
    Command,
    CommandError,
    CommandResult,
    CommandRunner,
    CommandStage,
    PipelineCommand,
)
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
        run_failures: ty.Iterable[BaseException | None] = (),
    ) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.checks: list[bool] = []
        self.pipelines: list[tuple[tuple[str, ...], ...]] = []
        self.returncodes = list(returncodes)
        self.stdouts = list(stdouts)
        self.pipeline_failures = list(pipeline_failures)
        self.run_failures = list(run_failures)

    def run(
        self,
        command: Command,
        *,
        capture_output: bool = False,
        check: bool = True,
    ) -> CommandResult:
        self.commands.append(tuple(str(argument) for argument in command))
        self.checks.append(check)
        failure = self.run_failures.pop(0) if self.run_failures else None
        if failure is not None:
            raise failure
        returncode = self.returncodes.pop(0) if self.returncodes else 0
        stdout = self.stdouts.pop(0) if self.stdouts else None
        if check and returncode:
            raise CommandError(command, returncode, "")
        return CommandResult(stdout, "", (returncode,))

    def pipeline(
        self,
        commands: ty.Sequence[PipelineCommand],
        *,
        capture_output: bool = False,
        check: bool = True,
    ) -> CommandResult:
        normalized = tuple(
            command.execution_command()
            if isinstance(command, CommandStage)
            else tuple(str(argument) for argument in command)
            for command in commands
        )
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


def replicated_operation() -> BackupOperation:
    return BackupOperation(
        "example",
        "hourly",
        datetime(2026, 8, 27, 12, 30),
        "yaesm-local-hourly.2026_08_27_12:30",
    )


def test_name():
    assert ZFSDriver.name() == "zfs"


def test_config_schema_accepts_shorthand():
    assert ZFSDriver.config_schema()("tank/home") == {"dataset": "tank/home"}


def test_config_schema_accepts_expanded_configuration():
    assert ZFSDriver.config_schema()({"dataset": "tank/home"}) == {"dataset": "tank/home"}


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
    assert driver.capability_metadata("store").adds == {
        DataProperty.ENCRYPTED,
        DataProperty.SNAPSHOT,
    }
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


def test_source_checks_dataset(tmp_path, monkeypatch):
    runner = RecordingRunner()
    monkeypatch.setattr(command_module, "run", runner.run)
    driver = ZFSDriver("tank/home")

    checks = driver.check(CheckRole.SOURCE)

    assert tuple(check.description for check in checks) == (
        "zfs is installed",
        "source dataset exists: tank/home",
    )
    assert runner.commands == []
    assert all(check.run().passed for check in checks)
    assert runner.commands == [
        ("zfs", "--version"),
        ("zfs", "list", "-H", "-t", "filesystem", "-o", "name", "tank/home"),
    ]


def test_destination_checks_dataset_parent_and_creation_remotely(tmp_path, monkeypatch):
    target = SSHTarget("ssh://host", tmp_path / "key")
    runner = RecordingRunner()
    monkeypatch.setattr(command_module, "run", runner.run)
    driver = ZFSDriver("tank/backups/home", target)

    checks = driver.check(CheckRole.DESTINATION)
    for check in checks:
        check.run()

    assert tuple(check.description for check in checks) == (
        f"zfs is installed on {target}",
        f"destination parent dataset exists: tank/backups on {target}",
        f"destination dataset can be created: tank/backups/home on {target}",
    )
    assert runner.commands == [
        target.openssh_command(("zfs", "--version")),
        target.openssh_command(
            ("zfs", "list", "-H", "-t", "filesystem", "-o", "name", "tank/backups")
        ),
        target.openssh_command(("zfs", "create", "-n", "-p", "-u", "tank/backups/home")),
    ]


def test_artifact_source_checks_existing_dataset():
    checks = ZFSDriver("tank/backups")._checks(CheckRole.ARTIFACT_SOURCE)

    assert tuple(check.description for check in checks) == ("source dataset exists: tank/backups",)


def test_encrypted_source_check_validates_encryption_property(monkeypatch):
    runner = RecordingRunner(stdouts=("aes-256-gcm\n",))
    monkeypatch.setattr(command_module, "run", runner.run)
    driver = ZFSDriver("tank/home", encryption=True)

    result = driver.check(CheckRole.SOURCE)[2].run()

    assert result.passed is True
    assert result.stdout == "aes-256-gcm\n"


@pytest.mark.parametrize("value", [None, "", "-\n", "off\n"])
def test_encrypted_source_check_rejects_unencrypted_dataset(value, monkeypatch):
    runner = RecordingRunner(stdouts=(value,))
    monkeypatch.setattr(command_module, "run", runner.run)
    driver = ZFSDriver("tank/home", encryption=True)

    result = driver.check(CheckRole.SOURCE)[2].run()

    assert result.passed is False
    assert result.failure == "ZFS dataset is not encrypted: tank/home"


@pytest.mark.parametrize(
    ("role", "encryption", "index"),
    [
        (CheckRole.SOURCE, False, 0),
        (CheckRole.SOURCE, True, 0),
        (CheckRole.SOURCE, True, 1),
        (CheckRole.ARTIFACT_SOURCE, False, 0),
        (CheckRole.ARTIFACT_SOURCE, True, 0),
        (CheckRole.ARTIFACT_SOURCE, True, 1),
        (CheckRole.DESTINATION, False, 0),
        (CheckRole.DESTINATION, False, 1),
    ],
)
def test_each_dataset_check_reports_command_failure(
    role,
    encryption,
    index,
    monkeypatch,
):
    monkeypatch.setattr(
        command_module,
        "run",
        lambda *args, **kwargs: CommandResult("off\n", "permission denied", (7,)),
    )
    check = ZFSDriver("tank/home", encryption=encryption)._checks(role)[index]

    result = check.run()

    assert result.description == check.description
    assert result.passed is False
    assert result.failure == "zfs exited with status 7"
    assert result.stdout == "off\n"
    assert result.stderr == "permission denied"


def test_destination_check_uses_pool_as_parent_for_top_level_dataset(monkeypatch):
    calls = []

    def run(command, *, capture_output=False, check=True):
        calls.append(command)
        return CommandResult(None, "", (0,))

    monkeypatch.setattr(command_module, "run", run)
    checks = ZFSDriver("tank")._checks(CheckRole.DESTINATION)
    for check in checks:
        check.run()

    assert tuple(check.description for check in checks) == (
        "destination parent dataset exists: tank",
        "destination dataset can be created: tank",
    )
    assert calls == [
        ("zfs", "list", "-H", "-t", "filesystem", "-o", "name", "tank"),
        ("zfs", "create", "-n", "-p", "-u", "tank"),
    ]


def test_transform_check_does_not_validate_unused_dataset():
    driver = ZFSDriver("tank/home")
    checks = driver.check(CheckRole.TRANSFORM)

    assert tuple(check.description for check in checks) == ("zfs is installed",)
    assert driver._checks(CheckRole.TRANSFORM) == ()


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


def test_cap_store_records_replication_source():
    runner = RecordingRunner()
    operation_ = replicated_operation()

    artifact = with_runner(ZFSDriver("tank/home"), runner).cap_store(
        ZFSDataset("tank/home"),
        operation_,
    )

    assert artifact.operation is operation_
    assert runner.commands[-1] == (
        "zfs",
        "set",
        f"yaesm:source-artifact={operation_.source_artifact_id}",
        f"tank/home@{operation_.artifact_name}",
    )


def test_cap_store_cleans_up_source_metadata_failure():
    runner = RecordingRunner(
        run_failures=(None, RuntimeError("metadata failed"), None),
    )
    operation_ = replicated_operation()

    with pytest.raises(RuntimeError, match="metadata failed"):
        with_runner(ZFSDriver("tank/home"), runner).cap_store(
            ZFSDataset("tank/home"),
            operation_,
        )

    assert runner.commands[-1] == (
        "zfs",
        "destroy",
        f"tank/home@{operation_.artifact_name}",
    )
    assert runner.checks[-1] is False


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

    assert stream == ZFSStream(
        (CommandStage(("zfs", "send", "-c", snapshot.name)),),
        suffixes=(".zfs",),
    )


def test_cap_export_incremental():
    base = ZFSSnapshot("tank/home", "base", guid=42)
    snapshot = ZFSSnapshot("tank/home", "snapshot")

    stream = ZFSDriver("tank/home").cap_export(snapshot, base)

    assert stream.stages == (CommandStage(("zfs", "send", "-c", "-i", base.name, snapshot.name)),)
    assert stream.base_guid == base.guid


def test_cap_export_uses_raw_send_for_native_encryption():
    runner = RecordingRunner(stdouts=("aes-256-gcm\n",))
    snapshot = ZFSSnapshot("tank/home", "snapshot")

    stream = with_runner(ZFSDriver("tank/home", encryption=True), runner).cap_export(snapshot)

    assert stream == ZFSStream(
        (CommandStage(("zfs", "send", "-w", snapshot.name)),),
        encrypted=True,
        suffixes=(".zfs",),
    )
    assert runner.commands == [("zfs", "get", "-H", "-o", "value", "encryption", "tank/home")]


def test_cap_export_uses_raw_incremental_send():
    base = ZFSSnapshot("tank/home", "base", encrypted=True, guid=42)
    snapshot = ZFSSnapshot("tank/home", "snapshot", encrypted=True)

    stream = ZFSDriver("tank/home").cap_export(snapshot, base)

    assert stream.stages == (CommandStage(("zfs", "send", "-w", "-i", base.name, snapshot.name)),)
    assert stream.base_guid == base.guid


def test_incremental_base_requires_matching_zfs_guids():
    source = ZFSSnapshot("tank/home", "current", guid=2)
    source_base = ZFSSnapshot("tank/home", "base", guid=1)
    destination_base = ZFSSnapshot("backup/home", "base", guid=1)

    assert ZFSDriver("tank/home").validate_base("export", source, source_base, destination_base)
    assert ZFSDriver("backup/home").validate_base(
        "import",
        ZFSStream((), base_guid=1),
        source_base,
        destination_base,
    )

    with pytest.raises(ZFSDriverError, match="different GUIDs"):
        ZFSDriver("tank/home").validate_base(
            "export",
            source,
            source_base,
            dataclasses.replace(destination_base, guid=3),
        )

    with pytest.raises(ZFSDriverError, match="stream does not match"):
        ZFSDriver("backup/home").validate_base(
            "import",
            ZFSStream((), base_guid=2),
            source_base,
            destination_base,
        )

    with pytest.raises(ZFSDriverError, match="source incremental base snapshot is missing"):
        ZFSDriver("tank/home").validate_base("export", source, None, destination_base)


def test_zfs_store_validates_source_snapshot_guid():
    old = operation(11)
    runner = RecordingRunner(stdouts=("42\n",))
    destination_base = ZFSSnapshot("backup/home", old.artifact_name, guid=42)
    driver = with_runner(ZFSDriver("backup/home"), runner)

    assert driver.validate_base(
        "store",
        ZFSDataset("tank/home"),
        None,
        destination_base,
    )
    assert runner.commands == [
        (
            "zfs",
            "get",
            "-H",
            "-o",
            "value",
            "guid",
            f"tank/home@{old.artifact_name}",
        )
    ]


def test_zfs_base_rejects_incompatible_representations():
    destination_base = ZFSSnapshot("backup/home", "base", guid=1)
    driver = ZFSDriver("backup/home")

    assert not driver.validate_base("store", Representation(), None, destination_base)
    assert not driver.validate_base(
        "store",
        ZFSDataset("backup/home"),
        None,
        destination_base,
    )
    assert not driver.validate_base(
        "export",
        ZFSSnapshot("tank/home", "current", guid=2),
        ZFSSnapshot("other/home", "base", guid=1),
        destination_base,
    )
    assert not driver.validate_base("import", Representation(), None, destination_base)
    assert not driver.validate_base("unknown", Representation(), None, destination_base)
    assert not driver.validate_base("store", Representation(), None, Representation())


def test_zfs_base_rejects_unreadable_guid():
    runner = RecordingRunner(stdouts=("invalid\n",))
    driver = with_runner(ZFSDriver("backup/home"), runner)

    with pytest.raises(ZFSDriverError, match="could not read ZFS snapshot GUID"):
        driver.validate_base(
            "store",
            ZFSDataset("tank/home"),
            None,
            ZFSSnapshot("backup/home", "base", guid=1),
        )


def test_cap_export_remote(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")
    snapshot = ZFSSnapshot("tank/home", "snapshot", target)

    stream = ZFSDriver("tank/home").cap_export(snapshot)

    assert stream.stages == (CommandStage(("zfs", "send", "-c", snapshot.name), target),)


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
    stream = ZFSStream((CommandStage(("zfs", "send", "tank/home@snapshot")),))

    artifact = with_runner(ZFSDriver("backup/home", target), runner).cap_import(stream, operation())

    assert artifact == BackupArtifact(
        operation(),
        ZFSSnapshot("backup/home", operation().artifact_name, target),
    )
    assert runner.pipelines == [
        (
            stream.stages[0].execution_command(),
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
    stream = ZFSStream((CommandStage(("zfs", "send", "tank/home@snapshot")),))

    with pytest.raises(ZFSDriverError, match="does not belong to the destination"):
        ZFSDriver("backup/home").cap_import(
            stream,
            operation(),
            ZFSSnapshot("other/home", "base"),
        )


def test_cap_import_cleans_up_failed_snapshot():
    runner = RecordingRunner(pipeline_failures=(RuntimeError("receive failed"),))
    stream = ZFSStream((CommandStage(("zfs", "send", "tank/home@snapshot")),))

    with pytest.raises(RuntimeError, match="receive failed"):
        with_runner(ZFSDriver("backup/home"), runner).cap_import(stream, operation())

    assert runner.commands == [("zfs", "destroy", f"backup/home@{operation().artifact_name}")]
    assert runner.checks == [False]


def test_cap_import_preserves_encryption_state():
    runner = RecordingRunner()
    stream = ZFSStream(
        (CommandStage(("zfs", "send", "-w", "tank/home@snapshot")),),
        encrypted=True,
    )

    artifact = with_runner(ZFSDriver("backup/home"), runner).cap_import(stream, operation())

    assert artifact.representation.encrypted is True


def test_cap_list_returns_matching_artifacts_newest_first():
    older = operation(11)
    source = "yaesm-local-hourly.2026_08_27_12:30"
    newer = BackupOperation("example", "hourly", datetime(2026, 8, 27, 12, 30), source)
    runner = RecordingRunner(
        stdouts=(
            "\n".join(
                (
                    f"backup/home@{older.artifact_name}\t11\t-",
                    "malformed",
                    f"backup/home@{older.artifact_name}\tinvalid\t-",
                    "backup/home@not-an-artifact\t12\t-",
                    f"other/home@{newer.artifact_name}\t13\t-",
                    f"backup/home@{newer.artifact_name}\t14\t{source}",
                )
            ),
        )
    )

    artifacts = with_runner(ZFSDriver("backup/home"), runner).cap_list("example")

    assert artifacts == (
        BackupArtifact(newer, ZFSSnapshot("backup/home", newer.artifact_name)),
        BackupArtifact(older, ZFSSnapshot("backup/home", older.artifact_name)),
    )
    assert tuple(artifact.representation.guid for artifact in artifacts) == (14, 11)
    assert ZFSDriver("backup/home").artifact_id(artifacts[0]) == "14"
    assert runner.commands == [
        (
            "zfs",
            "list",
            "-H",
            "-t",
            "snapshot",
            "-o",
            "name,guid,yaesm:source-artifact",
            "-d",
            "1",
            "backup/home",
        )
    ]
    assert runner.checks == [True]


def test_artifact_id_is_stable_before_and_after_listing():
    runner = RecordingRunner(stdouts=("42\n",))
    snapshot = ZFSSnapshot("backup/home", operation().artifact_name)
    driver = with_runner(ZFSDriver("backup/home"), runner)

    assert driver.artifact_id(BackupArtifact(operation(), snapshot)) == "42"
    assert (
        driver.artifact_id(BackupArtifact(operation(), dataclasses.replace(snapshot, guid=42)))
        == "42"
    )
    assert runner.commands == [
        ("zfs", "get", "-H", "-o", "value", "guid", snapshot.name),
    ]


def test_cap_list_propagates_command_failure():
    runner = RecordingRunner((1,))

    with pytest.raises(CommandError, match="command exited with status 1: zfs list"):
        with_runner(ZFSDriver("backup/home"), runner).cap_list("example")


def test_cap_list_returns_empty_when_destination_does_not_exist():
    error = CommandError(
        ("zfs", "list"),
        1,
        "cannot open 'backup/home': dataset does not exist",
    )
    runner = RecordingRunner(run_failures=(error,))

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
                "name,guid,yaesm:source-artifact",
                "-d",
                "1",
                "backup/home",
            )
        )
    ]


def test_formats_local_and_remote_artifact_locators(tmp_path):
    operation_ = operation()
    snapshot = ZFSSnapshot("backup/home", operation_.artifact_name)
    target = SSHTarget("ssh://host", tmp_path / "key")
    driver = ZFSDriver("backup/home")

    assert driver.format_locator(BackupArtifact(operation_, snapshot)) == snapshot.name
    assert driver.format_locator(
        BackupArtifact(
            operation_,
            ZFSSnapshot(snapshot.dataset, snapshot.snapshot, target),
        )
    ) == target.format_location(snapshot.name)


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


@pytest.mark.parametrize(("stdout", "expected"), [("", True), ("M\t/content\n", False)])
def test_cap_unchanged_compares_source_snapshots(stdout, expected):
    runner = RecordingRunner(stdouts=("42", stdout))
    driver = with_runner(ZFSDriver("backup/home"), runner)
    source = ZFSSnapshot("tank/home", "current")
    previous = BackupArtifact(
        operation(11),
        ZFSSnapshot("backup/home", operation(11).artifact_name, guid=42),
    )

    assert driver.cap_unchanged(source, previous) is expected

    assert runner.commands == [
        (
            "zfs",
            "get",
            "-H",
            "-o",
            "value",
            "guid",
            f"tank/home@{operation(11).artifact_name}",
        ),
        (
            "zfs",
            "diff",
            "-H",
            f"tank/home@{operation(11).artifact_name}",
            "tank/home@current",
        ),
    ]


def test_cap_unchanged_rejects_mismatched_snapshot_guid():
    runner = RecordingRunner(stdouts=("41",))
    source = ZFSSnapshot("tank/home", "current")
    previous = BackupArtifact(
        operation(11),
        ZFSSnapshot("backup/home", operation(11).artifact_name, guid=42),
    )

    with pytest.raises(ZFSDriverError, match="snapshots have different GUIDs"):
        with_runner(ZFSDriver("backup/home"), runner).cap_unchanged(source, previous)

    assert all("diff" not in command for command in runner.commands)


def test_cap_unchanged_rejects_artifact_outside_destination():
    source = ZFSSnapshot("tank/home", "current")
    previous = BackupArtifact(operation(), ZFSSnapshot("other/home", "previous"))

    with pytest.raises(ZFSDriverError, match="does not belong to the destination"):
        ZFSDriver("backup/home").cap_unchanged(source, previous)


def test_cap_cleanup_destroys_snapshot():
    runner = RecordingRunner()

    with_runner(ZFSDriver("tank/home"), runner).cap_cleanup(ZFSSnapshot("tank/home", "staging"))

    assert runner.commands == [("zfs", "destroy", "tank/home@staging")]


def test_backup_execute_uses_validated_zfs_incremental_base():
    old = operation(11)
    runner = RecordingRunner(
        stdouts=(
            f"backup/home@{old.artifact_name}\t42\t-\n",
            "42\n",
        )
    )
    backup = Backup(
        "example",
        ZFSDriver("tank/home"),
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


def test_backup_execute_rejects_mismatched_zfs_base_guids():
    old = operation(11)
    runner = RecordingRunner(
        stdouts=(
            f"backup/home@{old.artifact_name}\t42\t-\n",
            "43\n",
        )
    )
    backup = Backup(
        "example",
        ZFSDriver("tank/home"),
        with_runner(ZFSDriver("backup/home"), runner),
    )

    with pytest.raises(BackupError, match="failed in zfs.store"):
        backup.execute("hourly", operation().created_at)

    assert runner.pipelines == []


def test_backup_execute_preserves_configured_native_encryption():
    source_runner = RecordingRunner(stdouts=("aes-256-gcm\n",))
    destination_runner = RecordingRunner()
    backup = Backup(
        "example",
        with_runner(ZFSDriver("tank/home", encryption=True), source_runner),
        with_runner(ZFSDriver("backup/home"), destination_runner),
    )

    artifact = backup.execute("hourly", operation().created_at)

    source = f"tank/home@{operation().artifact_name}"
    assert destination_runner.pipelines[0][0] == ("zfs", "send", "-w", source)
    assert artifact.representation.encrypted is True


def test_zfs_representation_types():
    assert isinstance(ZFSDataset("tank/home"), Representation)
    assert isinstance(ZFSSnapshot("tank/home", "snapshot"), Representation)
    assert ZFSStream.__bases__ == (CommandStream,)
    assert ZFSStream.suffix == ".zfs"


@pytest.fixture
def zfs_pools(tmp_path: ty.Path) -> ty.Iterator[tuple[str, str]]:
    if shutil.which("zfs") is None or shutil.which("zpool") is None:
        pytest.skip("ZFS is not installed")
    if os.geteuid() != 0:
        pytest.skip("ZFS integration tests require root")

    prefix = f"yaesm_test_{uuid4().hex[:10]}"
    pools = (f"{prefix}_source", f"{prefix}_destination")
    created = []
    try:
        for pool in pools:
            image = tmp_path / f"{pool}.img"
            with image.open("wb") as file:
                file.truncate(256 * 1024 * 1024)
            result = subprocess.run(
                ("zpool", "create", "-f", "-m", "none", pool, str(image)),
                capture_output=True,
                check=False,
                text=True,
            )
            if result.returncode:
                pytest.fail(f"could not create temporary ZFS pool: {result.stderr.strip()}")
            created.append(pool)
        yield pools
    finally:
        for pool in reversed(created):
            subprocess.run(
                ("zpool", "destroy", "-f", pool),
                capture_output=True,
                check=False,
            )


def test_zfs_full_incremental_and_lifecycle(
    tmp_path: ty.Path,
    zfs_pools: tuple[str, str],
) -> None:
    source_pool, destination_pool = zfs_pools
    source_dataset = f"{source_pool}/source"
    destination_dataset = f"{destination_pool}/backup"
    source_path = tmp_path / "source"
    _run("zfs", "create", "-o", f"mountpoint={source_path}", source_dataset)

    source_driver = ZFSDriver(source_dataset)
    destination_driver = ZFSDriver(destination_dataset)
    backup = Backup(
        "example",
        source_driver,
        destination_driver,
    )
    created_at = datetime(2026, 8, 27, 12, 30)

    (source_path / "content").write_text("first")
    first = backup.execute("manual", created_at)
    (source_path / "content").write_text("second")
    second = backup.execute("manual", created_at + timedelta(minutes=1))

    assert destination_driver.cap_list("example") == (second, first)
    assert _snapshots(source_dataset) == {f"{source_dataset}@{second.name}"}

    destination_path = tmp_path / "destination"
    _mount(destination_dataset, destination_path)
    assert (destination_path / "content").read_text() == "second"

    destination_driver.cap_delete((first, second))
    assert destination_driver.cap_list("example") == ()


def test_zfs_skip_unchanged_integration(
    tmp_path: ty.Path,
    zfs_pools: tuple[str, str],
) -> None:
    source_pool, destination_pool = zfs_pools
    source_dataset = f"{source_pool}/source"
    destination_dataset = f"{destination_pool}/backup"
    source_path = tmp_path / "source"
    _run("zfs", "create", "-o", f"mountpoint={source_path}", source_dataset)

    destination_driver = ZFSDriver(destination_dataset)
    backup = Backup(
        "example",
        ZFSDriver(source_dataset),
        destination_driver,
        skip_unchanged=True,
    )
    created_at = datetime(2026, 8, 27, 12, 30)
    (source_path / "content").write_text("unchanged")

    first = backup.execute("manual", created_at)
    second = backup.execute("manual", created_at + timedelta(minutes=1))

    assert second == first
    assert destination_driver.cap_list("example") == (first,)

    (source_path / "content").write_text("changed content")
    third = backup.execute("manual", created_at + timedelta(minutes=2))

    assert third != first
    assert destination_driver.cap_list("example") == (third, first)


def test_zfs_checks_existing_source_and_new_destination(
    zfs_pools: tuple[str, str],
) -> None:
    source_pool, destination_pool = zfs_pools
    source_dataset = f"{source_pool}/source"
    _run("zfs", "create", "-u", source_dataset)

    source_results = tuple(
        check.run() for check in ZFSDriver(source_dataset).check(CheckRole.SOURCE)
    )
    destination_results = tuple(
        check.run()
        for check in ZFSDriver(f"{destination_pool}/backup").check(CheckRole.DESTINATION)
    )

    assert all(result.passed for result in (*source_results, *destination_results))


def test_zfs_raw_encrypted_full_and_incremental(
    tmp_path: ty.Path,
    zfs_pools: tuple[str, str],
) -> None:
    source_pool, destination_pool = zfs_pools
    source_dataset = f"{source_pool}/source"
    destination_dataset = f"{destination_pool}/backup"
    source_path = tmp_path / "encrypted-source"
    key = tmp_path / "zfs-key"
    key.write_text("integration-test-passphrase")
    key.chmod(0o600)
    _run(
        "zfs",
        "create",
        "-o",
        "encryption=aes-256-gcm",
        "-o",
        "keyformat=passphrase",
        "-o",
        f"keylocation={key.as_uri()}",
        "-o",
        f"mountpoint={source_path}",
        source_dataset,
    )

    backup = Backup(
        "encrypted",
        ZFSDriver(source_dataset, encryption=True),
        ZFSDriver(destination_dataset),
    )
    created_at = datetime(2026, 8, 27, 12, 30)

    (source_path / "content").write_text("first")
    backup.execute("manual", created_at)
    (source_path / "content").write_text("second")
    second = backup.execute("manual", created_at + timedelta(minutes=1))

    assert second.representation.encrypted is True
    assert _property(destination_dataset, "encryption") == "aes-256-gcm"
    if _property(destination_dataset, "keystatus") != "available":
        _run("zfs", "load-key", "-L", key.as_uri(), destination_dataset)

    destination_path = tmp_path / "encrypted-destination"
    _mount(destination_dataset, destination_path)
    assert (destination_path / "content").read_text() == "second"


def test_zfs_preserves_native_compression_by_default(
    tmp_path: ty.Path,
    zfs_pools: tuple[str, str],
) -> None:
    source_pool, destination_pool = zfs_pools
    source_dataset = f"{source_pool}/source"
    destination_dataset = f"{destination_pool}/backup"
    source_path = tmp_path / "compressed-source"
    _run(
        "zfs",
        "create",
        "-o",
        "compression=lz4",
        "-o",
        f"mountpoint={source_path}",
        source_dataset,
    )

    backup = Backup(
        "compressed",
        ZFSDriver(source_dataset),
        ZFSDriver(destination_dataset),
    )
    created_at = datetime(2026, 8, 27, 12, 30)

    content = source_path / "content"
    content.write_bytes(b"a" * 1024 * 1024)
    backup.execute("manual", created_at)
    content.write_bytes(b"b" * 1024 * 1024)
    backup.execute("manual", created_at + timedelta(minutes=1))

    assert float(_property(destination_dataset, "refcompressratio").removesuffix("x")) > 1

    destination_path = tmp_path / "compressed-destination"
    _mount(destination_dataset, destination_path)
    assert (destination_path / "content").read_bytes() == b"b" * 1024 * 1024


def _run(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, check=True, text=True)


def _property(dataset: str, name: str) -> str:
    return _run("zfs", "get", "-H", "-o", "value", name, dataset).stdout.strip()


def _snapshots(dataset: str) -> set[str]:
    output = _run(
        "zfs",
        "list",
        "-H",
        "-t",
        "snapshot",
        "-o",
        "name",
        "-d",
        "1",
        dataset,
    ).stdout
    return set(output.splitlines())


def _mount(dataset: str, path: ty.Path) -> None:
    _run("zfs", "set", f"mountpoint={path}", dataset)
    if _property(dataset, "mounted") != "yes":
        _run("zfs", "mount", dataset)
