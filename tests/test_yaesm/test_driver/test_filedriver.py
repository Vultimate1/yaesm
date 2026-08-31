"""Tests for yaesm.driver.filedriver."""

from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
import voluptuous as vlp

import yaesm.command as command_module
import yaesm.driver.filedriver as file_module
import yaesm.ty as ty
from yaesm.backup import BackupArtifact, BackupOperation
from yaesm.check import CheckRole
from yaesm.command import (
    Command,
    CommandError,
    CommandResult,
    CommandRunner,
    CommandStage,
    PipelineCommand,
)
from yaesm.driver.directorydriver import DirectoryDriver
from yaesm.driver.filedriver import FileDriver, FileDriverError, FileStream
from yaesm.driver.tardriver import TarDriver
from yaesm.pipeline import Pipeline, PipelineError, PipelineStep
from yaesm.representation import CommandStream
from yaesm.ssh import SSHTarget


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
        {"location": "/source"},
    ],
)
def test_config_schema_accepts_absolute_location(value):
    assert FileDriver.config_schema()(value) == {"location": Path("/source")}


@pytest.mark.parametrize(
    "value",
    [
        "relative",
        Path("relative"),
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
    tar = TarDriver(tmp_path)
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
        (TarDriver(Path("/unused")),),
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
