"""Tests for yaesm.driver.filedriver."""

import dataclasses
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
import voluptuous as vlp

import yaesm.command as command_module
import yaesm.driver.filedriver as file_module
import yaesm.ty as ty
from yaesm.backup import Backup, BackupArtifact, BackupError, BackupOperation, BackupSource
from yaesm.check import CheckRole
from yaesm.command import (
    Command,
    CommandError,
    CommandResult,
    CommandRunner,
    CommandStage,
    PipelineCommand,
)
from yaesm.config import parse_config
from yaesm.driver.directorydriver import DirectoryDriver
from yaesm.driver.filedriver import FileDriver, FileDriverError, FileStream
from yaesm.driver.tardriver import TarDriver
from yaesm.pipeline import Pipeline, PipelineError, PipelineStep
from yaesm.representation import CommandStream
from yaesm.ssh import SSHTarget
from yaesm.subcommand.checksubcommand import CheckSubcommand


class RecordingRunner(CommandRunner):
    def __init__(
        self,
        *,
        run_failures: ty.Iterable[BaseException | None] = (),
        pipeline_failure: BaseException | None = None,
        stdouts: ty.Iterable[str | None] = (),
    ) -> None:
        self.run_calls: list[tuple[tuple[str, ...], bool, bool]] = []
        self.pipeline_calls: list[tuple[tuple[tuple[str, ...], ...], bool, bool]] = []
        self.run_failures = list(run_failures)
        self.pipeline_failure = pipeline_failure
        self.stdouts = list(stdouts)

    def run(
        self,
        command: Command,
        *,
        capture_output: bool = False,
        check: bool = True,
    ) -> CommandResult:
        normalized = tuple(str(argument) for argument in command)
        self.run_calls.append((normalized, capture_output, check))
        if self.run_failures:
            failure = self.run_failures.pop(0)
            if failure is not None:
                raise failure
        stdout = self.stdouts.pop(0) if self.stdouts else None
        return CommandResult(stdout, "", (0,))

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
        self.pipeline_calls.append((normalized, capture_output, check))
        if self.pipeline_failure is not None:
            raise self.pipeline_failure
        return CommandResult(None, "", (0,) * len(normalized))


class ShellSSHRunner(CommandRunner):
    def pipeline(self, commands, **options):
        commands = tuple(
            command.execution_command() if isinstance(command, CommandStage) else command
            for command in commands
        )
        return super().pipeline(
            tuple(
                ("sh", "-c", command[-1]) if command[0] == "ssh" else command
                for command in commands
            ),
            **options,
        )


def operation(offset: int = 0) -> BackupOperation:
    return BackupOperation(
        "example",
        "manual",
        datetime(2026, 8, 27, 12, 30) + timedelta(minutes=offset),
    )


def artifact(path: ty.Path, ssh: SSHTarget | None = None) -> BackupArtifact[FileStream]:
    return BackupArtifact(operation(), FileStream(path, ssh))


def test_name():
    assert FileDriver.name() == "file"


@pytest.mark.parametrize(
    "value",
    [
        "/source",
        Path("/source"),
    ],
)
def test_config_schema_accepts_absolute_location(value):
    assert FileDriver.config_schema()(value) == {"location": Path("/source")}


@pytest.mark.parametrize(
    "value",
    [
        "relative",
        Path("relative"),
        {"location": "/source"},
        {"location": "relative"},
        {"location": 1},
        {},
        {"location": "/source", "unknown": True},
    ],
)
def test_config_schema_rejects_invalid_configuration(value):
    with pytest.raises(vlp.Invalid):
        FileDriver.config_schema()(value)


def test_capabilities_cover_file_source_and_artifact_lifecycle(tmp_path):
    assert FileDriver(tmp_path).capabilities() == {"source", "import", "list", "delete"}


def test_cap_source_reads_file_and_preserves_suffixes(tmp_path):
    path = tmp_path / "database.sql.zst"

    stream = FileDriver(path).cap_source()

    assert stream == FileStream(path, suffixes=(".sql", ".zst"))
    assert stream.stages == (CommandStage(("cat", path)),)


def test_cap_source_reads_file_remotely(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")
    path = Path("/source/data")

    stream = FileDriver(path, target).cap_source()

    assert stream.path == path
    assert stream.ssh is target
    assert stream.stages == (CommandStage(("cat", path), target),)


@pytest.mark.parametrize(
    ("role", "descriptions", "flags"),
    [
        (CheckRole.SOURCE, ("file exists", "file is readable"), ("-f", "-r")),
        (
            CheckRole.ARTIFACT_SOURCE,
            ("directory exists", "directory is readable", "directory is searchable"),
            ("-d", "-r", "-x"),
        ),
        (
            CheckRole.DESTINATION,
            (
                "directory exists",
                "directory is readable",
                "directory is writable",
                "directory is searchable",
            ),
            ("-d", "-r", "-w", "-x"),
        ),
    ],
)
def test_role_checks_run_remotely(role, descriptions, flags, tmp_path, monkeypatch):
    target = SSHTarget("ssh://host", tmp_path / "key")
    runner = RecordingRunner()
    monkeypatch.setattr(command_module, "run", runner.run)
    checks = FileDriver(Path("/location"), target).check(role)

    assert tuple(check.description for check in checks) == tuple(
        f"{description}: /location on {target}" for description in descriptions
    )
    assert all(check.run().passed for check in checks)
    assert tuple(call[0] for call in runner.run_calls) == tuple(
        target.openssh_command(("test", flag, Path("/location"))) for flag in flags
    )


def test_transform_role_has_no_checks(tmp_path):
    assert FileDriver(tmp_path).check(CheckRole.TRANSFORM) == ()


def test_cap_import_stores_stream_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(file_module, "uuid4", lambda: UUID(int=1))
    runner = RecordingRunner()
    driver = FileDriver(tmp_path)
    driver.runner = runner
    source = CommandStream(
        (CommandStage(("produce", "data")),),
        suffixes=(".tar", ".zst"),
    )

    result = driver.cap_import(source, operation())

    artifact_name = f"{operation().artifact_name}.tar.zst"
    destination = tmp_path / artifact_name
    temporary = tmp_path / f".{artifact_name}.tmp-{UUID(int=1).hex}"
    assert result == BackupArtifact(
        operation(),
        FileStream(destination, suffixes=(".tar", ".zst")),
    )
    assert runner.pipeline_calls == [
        (
            (
                ("produce", "data"),
                ("dd", f"of={temporary}", "bs=1048576"),
            ),
            False,
            True,
        )
    ]
    assert runner.run_calls == [(("mv", str(temporary), str(destination)), False, True)]


def test_cap_import_writes_on_remote_target(tmp_path, monkeypatch):
    monkeypatch.setattr(file_module, "uuid4", lambda: UUID(int=1))
    target = SSHTarget("ssh://host", tmp_path / "key")
    runner = RecordingRunner()
    driver = FileDriver(Path("/archives"), target)
    driver.runner = runner

    result = driver.cap_import(CommandStream((CommandStage(("produce",)),)), operation())

    destination = Path("/archives") / operation().artifact_name
    temporary = Path("/archives") / f".{operation().artifact_name}.tmp-{UUID(int=1).hex}"
    assert result.representation == FileStream(destination, target)
    assert runner.pipeline_calls[0][0] == (
        ("produce",),
        target.openssh_command(("dd", f"of={temporary}", "bs=1048576")),
    )
    assert runner.run_calls[0][0] == target.openssh_command(("mv", temporary, destination))


def test_cap_import_removes_temporary_after_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(file_module, "uuid4", lambda: UUID(int=1))
    error = CommandError(("produce",), 1, "failed")
    runner = RecordingRunner(pipeline_failure=error)
    driver = FileDriver(tmp_path)
    driver.runner = runner

    with pytest.raises(CommandError) as raised:
        driver.cap_import(CommandStream((CommandStage(("produce",)),)), operation())

    assert raised.value is error
    temporary = tmp_path / f".{operation().artifact_name}.tmp-{UUID(int=1).hex}"
    assert runner.run_calls == [(("rm", "-f", str(temporary)), False, False)]


def test_cap_list_returns_newest_files_with_suffixes(tmp_path):
    old = tmp_path / operation().artifact_name
    new = tmp_path / f"{operation(1).artifact_name}.sql.zst.gpg"
    runner = RecordingRunner(stdouts=(f"{old}\n{tmp_path / 'unrelated'}\n{new}\n",))
    driver = FileDriver(tmp_path)
    driver.runner = runner

    artifacts = driver.cap_list("example")

    assert tuple(item.representation.path for item in artifacts) == (new, old)
    assert artifacts[0].representation.suffixes == (".sql", ".zst", ".gpg")
    assert artifacts[1].representation.suffixes == ()


def test_format_locator(tmp_path):
    local = artifact(tmp_path / "local")
    target = SSHTarget("ssh://host", tmp_path / "key")
    remote = artifact(Path("/remote"), target)
    driver = FileDriver(tmp_path)

    assert driver.format_locator(local) == str(tmp_path / "local")
    assert driver.format_locator(remote) == f"{target}/remote"


def test_cap_delete_batches_files(tmp_path):
    runner = RecordingRunner()
    driver = FileDriver(tmp_path)
    driver.runner = runner

    driver.cap_delete((artifact(tmp_path / "one"), artifact(tmp_path / "two")))

    assert runner.run_calls == [
        (("rm", "-f", str(tmp_path / "one"), str(tmp_path / "two")), False, True),
    ]


def test_cap_delete_does_nothing_without_files(tmp_path):
    runner = RecordingRunner()
    driver = FileDriver(tmp_path)
    driver.runner = runner

    driver.cap_delete(())

    assert runner.run_calls == []


def test_cap_delete_rejects_different_endpoint(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")

    with pytest.raises(FileDriverError, match="different OpenSSH endpoint"):
        FileDriver(tmp_path).cap_delete((artifact(Path("/archive"), target),))


def test_file_pipeline_uses_source_and_destination_without_transform(tmp_path):
    source = FileDriver(Path("/source/data"))
    destination = FileDriver(tmp_path)

    pipeline = Pipeline(source, destination)

    assert pipeline.steps == (
        PipelineStep(source, "source"),
        PipelineStep(destination, "import"),
    )


def test_file_destination_can_store_a_tar_transform(tmp_path):
    source = DirectoryDriver(Path("/source"))
    tar = TarDriver()
    destination = FileDriver(tmp_path)

    pipeline = Pipeline(source, destination, (tar,))

    assert pipeline.steps == (
        PipelineStep(source, "source"),
        PipelineStep(tar, "export"),
        PipelineStep(destination, "import"),
    )


def test_file_destination_inside_source_is_excluded_from_tar():
    runner = RecordingRunner()
    destination = FileDriver(Path("/source/backups"))
    destination.runner = runner

    Pipeline(
        DirectoryDriver(Path("/source")),
        destination,
        (TarDriver(),),
    ).execute(operation())

    tar_command = runner.pipeline_calls[0][0][0]
    assert "--exclude=./backups" in tar_command


def test_file_cannot_be_used_as_a_transform(tmp_path):
    with pytest.raises(PipelineError, match="next configured transform cannot be used: file"):
        Pipeline(
            FileDriver(Path("/source/data")),
            FileDriver(tmp_path),
            (FileDriver(Path("/unused")),),
        )


def test_stored_file_can_be_replicated(tmp_path):
    source_storage = FileDriver(Path("/source"))
    destination = FileDriver(tmp_path)
    source_artifact = BackupArtifact(
        operation(),
        FileStream(Path("/source/archive.tar.zst"), suffixes=(".tar", ".zst")),
    )

    pipeline = Pipeline(source_storage, destination, source_artifact=source_artifact)

    assert pipeline.steps == (PipelineStep(destination, "import"),)


def test_file_pipeline_copies_exact_bytes(tmp_path):
    source = tmp_path / "database.sql"
    destination = tmp_path / "backups"
    source.write_bytes(b"\x00backup data\xff")
    destination.mkdir()
    driver = FileDriver(destination)

    result = Pipeline(FileDriver(source), driver).execute(operation())

    assert result.representation.path.read_bytes() == source.read_bytes()
    assert result.representation.path.name == f"{operation().artifact_name}.sql"
    assert driver.cap_list("example") == (result,)


@pytest.mark.parametrize("home_schedule", ["manual", "docs-manual"])
def test_retention_keeps_backups_with_shared_name_prefix_separate(tmp_path, home_schedule):
    source = tmp_path / "source.txt"
    source.write_text("backup content")
    destination = tmp_path / "backups"
    destination.mkdir()
    config = parse_config(
        {
            name: {
                "source": {"file": str(source)},
                "destination": {"file": str(destination)},
                "schedules": {schedule: {"trigger": "on-demand", "retention": {"keep-last": 1}}},
            }
            for name, schedule in (("home", home_schedule), ("home-docs", "manual"))
        }
    )
    home = config.backups["home"]
    docs = config.backups["home-docs"]
    created_at = datetime(2026, 8, 27, 12, 30)

    first_docs = docs.execute("manual", created_at)
    first_home = home.execute(home_schedule, created_at)
    assert docs.artifacts() == (first_docs,)
    assert home.artifacts() == (first_home,)

    later = created_at + timedelta(minutes=1)
    second_docs = docs.execute("manual", later)
    assert not first_docs.representation.path.exists()
    assert first_home.representation.path.read_text() == "backup content"

    second_home = home.execute(home_schedule, later)
    assert not first_home.representation.path.exists()
    assert second_docs.representation.path.read_text() == "backup content"
    assert docs.artifacts() == (second_docs,)
    assert home.artifacts() == (second_home,)


@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("initial_skip_unchanged", [False, True])
def test_file_replication_skips_unchanged_source_across_schedules(
    tmp_path, remote, initial_skip_unchanged
):
    source = tmp_path / "source.bin"
    source.write_bytes(b"\x00first backup\xff")
    local_path = tmp_path / "local"
    offsite_path = tmp_path / "offsite '$(false)"
    local_path.mkdir()
    offsite_path.mkdir()
    target = SSHTarget("ssh://host", tmp_path / "key") if remote else None

    def driver(path):
        result = FileDriver(path, target)
        result.runner = ShellSSHRunner()
        return result

    local = Backup("local", driver(source), driver(local_path))
    first_source = local.execute("hourly", operation().created_at)
    replica = Backup(
        "offsite",
        BackupSource("local"),
        driver(offsite_path),
        skip_unchanged=initial_skip_unchanged,
    )
    first = replica.execute("daily", operation().created_at, {"local": local})

    # Recover identity from disk, including when change detection was enabled later.
    replica = Backup("offsite", BackupSource("local"), driver(offsite_path), skip_unchanged=True)
    assert replica.artifacts() == (first,)
    assert replica.execute("weekly", operation(1).created_at, {"local": local}) == first
    assert tuple(offsite_path.iterdir()) == (first.representation.path,)

    # Different source schedules can produce distinct artifacts at the same timestamp.
    local.destination.cap_delete((first_source,))
    source.write_bytes(b"\x00second backup\xff")
    second_source = local.execute("manual", operation().created_at)
    with pytest.raises(BackupError, match="already has artifact"):
        replica.execute("daily", operation(2).created_at, {"local": local})
    assert tuple(offsite_path.iterdir()) == (first.representation.path,)
    second = replica.execute("weekly", operation(2).created_at, {"local": local})
    assert second.operation.source_artifact_id == local.destination.artifact_id(second_source)
    assert first.representation.path.read_bytes() == b"\x00first backup\xff"
    assert second.representation.path.read_bytes() == source.read_bytes()
    assert len(tuple(offsite_path.iterdir())) == 2

    moved = tmp_path / "moved"
    moved.mkdir()
    shutil.copy2(second.representation.path, moved)
    assert FileDriver(moved).cap_list("offsite") == (
        dataclasses.replace(
            second,
            representation=FileStream(moved / second.representation.path.name, suffixes=(".bin",)),
        ),
    )

    # Losing metadata must cause a copy, never a false unchanged result.
    copy = tmp_path / "copy-without-metadata"
    shutil.copyfile(second.representation.path, copy)
    copy.replace(second.representation.path)
    third = replica.execute("monthly", operation(3).created_at, {"local": local})
    assert third.representation.path != second.representation.path
    assert third.representation.path.read_bytes() == source.read_bytes()


@pytest.mark.parametrize("skip_unchanged", [False, True])
def test_file_requires_working_metadata_only_for_skip_unchanged(
    tmp_path, monkeypatch, skip_unchanged
):
    destination = tmp_path / "backups"
    destination.mkdir()
    started = tmp_path / "transfer-started"
    source = CommandStream(
        (CommandStage(("sh", "-c", 'touch "$1"; printf payload', "sh", started)),)
    )
    operation_ = dataclasses.replace(
        operation(), source_artifact_id="source-id", skip_unchanged=skip_unchanged
    )
    driver = FileDriver(destination)
    monkeypatch.setattr(driver._source_id, "write", lambda path, source_id: False)

    if skip_unchanged:
        with pytest.raises(FileDriverError, match="skip_unchanged requires.*extended attributes"):
            driver.cap_import(source, operation_)
        assert not started.exists()
        assert not tuple(destination.iterdir())
    else:
        result = driver.cap_import(source, operation_)
        assert started.exists()
        assert result.representation.path.read_bytes() == b"payload"
        assert tuple(destination.iterdir()) == (result.representation.path,)


@pytest.mark.parametrize("skip_unchanged", [False, True])
def test_file_xattr_check_is_conditional_and_uses_destination_endpoint(tmp_path, skip_unchanged):
    local = Backup("local", FileDriver(tmp_path / "source"), FileDriver(tmp_path / "local"))
    target = SSHTarget("ssh://host", tmp_path / "key")
    replica = Backup(
        "offsite",
        BackupSource("local"),
        FileDriver(tmp_path / "offsite", target),
        skip_unchanged=skip_unchanged,
    )

    checks = CheckSubcommand._backup_checks(replica, {"local": local})

    metadata_checks = [check for check in checks if check.description.startswith("xattr tools")]
    assert len(metadata_checks) == int(skip_unchanged)
    assert all(check.ssh == target for check in metadata_checks)
