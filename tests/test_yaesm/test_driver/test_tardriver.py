"""Tests for yaesm.driver.tardriver."""

import shutil
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
import voluptuous as vlp

import yaesm.command as command_module
import yaesm.driver.tardriver as tar_module
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
from yaesm.driver.gpgdriver import GPGDriver
from yaesm.driver.rsyncdriver import RsyncDriver
from yaesm.driver.tardriver import TarArchive, TarDriver, TarDriverError, TarStream
from yaesm.driver.zstddriver import ZstdDriver
from yaesm.errors import YaesmValueError
from yaesm.pipeline import Pipeline, PipelineStep
from yaesm.representation import (
    CommandStream,
    DataProperty,
    PathTree,
    Representation,
    UncompressedStream,
)
from yaesm.ssh import SSHTarget

_TAR_COMMAND = (
    "tar",
    "-c",
    "-f",
    "-",
    "--format=pax",
    "--acls",
    "--xattrs",
    "--numeric-owner",
    "--one-file-system",
    "-C",
    "/source",
    ".",
)


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


def artifact(path: ty.Path, ssh: SSHTarget | None = None) -> BackupArtifact[TarArchive]:
    return BackupArtifact(operation(), TarArchive(path, ssh))


def test_name():
    assert TarDriver.name() == "tar"


def test_config_schema(tmp_path):
    assert TarDriver.config_schema()({"location": str(tmp_path)}) == {"location": tmp_path}


def test_config_schema_accepts_path_location(tmp_path):
    assert TarDriver.config_schema()({"location": tmp_path}) == {"location": tmp_path}


def test_config_schema_accepts_shorthand(tmp_path):
    assert TarDriver.config_schema()(tmp_path) == {"location": tmp_path}


def test_config_schema_accepts_one_file_system(tmp_path):
    assert TarDriver.config_schema()({"location": tmp_path, "one_file_system": False}) == {
        "location": tmp_path,
        "one_file_system": False,
    }


@pytest.mark.parametrize("location", [None, 42])
def test_config_schema_rejects_invalid_location_type(location):
    with pytest.raises(vlp.Invalid, match="location must be a path"):
        TarDriver.config_schema()({"location": location})


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"location": "relative"},
        {"location": "/tmp", "ssh": None},
        {"location": "/tmp", "ssh": "ssh://host"},
        {"location": "/tmp", "one_file_system": None},
        {"location": "/tmp", "one_file_system": 1},
        {"location": "/tmp", "one_file_system": "yes"},
        {"location": "/tmp", "unknown": True},
    ],
)
def test_config_schema_rejects_invalid_configuration(config):
    with pytest.raises(vlp.Invalid):
        TarDriver.config_schema()(config)


def test_config_schema_output_constructs_driver(tmp_path):
    config = TarDriver.config_schema()({"location": tmp_path})

    driver = TarDriver(**config)

    assert driver.location == tmp_path
    assert driver.ssh is None


def test_constructor_rejects_invalid_one_file_system(tmp_path):
    with pytest.raises(YaesmValueError, match="one_file_system must be a boolean"):
        TarDriver(tmp_path, one_file_system=ty.cast(bool, "yes"))


def test_destination_checks_directory_requirements(tmp_path, monkeypatch):
    runner = RecordingRunner()
    monkeypatch.setattr(command_module, "run", runner.run)
    checks = TarDriver(tmp_path).check(CheckRole.DESTINATION)

    assert tuple(check.description for check in checks) == (
        "tar is installed",
        f"directory exists: {tmp_path}",
        f"directory is readable: {tmp_path}",
        f"directory is writable: {tmp_path}",
        f"directory is searchable: {tmp_path}",
    )
    assert runner.run_calls == []
    assert all(check.run().passed for check in checks)
    assert tuple(call[0] for call in runner.run_calls) == (
        ("tar", "--version"),
        ("test", "-d", str(tmp_path)),
        ("test", "-r", str(tmp_path)),
        ("test", "-w", str(tmp_path)),
        ("test", "-x", str(tmp_path)),
    )


def test_destination_checks_run_remotely(tmp_path, monkeypatch):
    target = SSHTarget("ssh://host", tmp_path / "key")
    runner = RecordingRunner()
    monkeypatch.setattr(command_module, "run", runner.run)

    for check in TarDriver(tmp_path, target).check(CheckRole.DESTINATION):
        check.run()

    assert tuple(call[0] for call in runner.run_calls) == tuple(
        target.openssh_command(command)
        for command in (
            ("tar", "--version"),
            ("test", "-d", tmp_path),
            ("test", "-r", tmp_path),
            ("test", "-w", tmp_path),
            ("test", "-x", tmp_path),
        )
    )


@pytest.mark.parametrize(
    "role",
    [CheckRole.SOURCE, CheckRole.ARTIFACT_SOURCE, CheckRole.TRANSFORM],
)
def test_non_destination_checks_only_require_tar(role, tmp_path):
    driver = TarDriver(tmp_path)

    assert driver._checks(role) == ()
    assert tuple(check.description for check in driver.check(role)) == ("tar is installed",)


def test_cap_export_creates_complete_portable_archive(tmp_path):
    stream = TarDriver(tmp_path).cap_export(PathTree(Path("/source")))

    assert stream == TarStream((CommandStage(_TAR_COMMAND),), suffixes=(".tar",))


def test_cap_export_can_cross_filesystem_boundaries(tmp_path):
    stream = TarDriver(tmp_path, one_file_system=False).cap_export(PathTree(Path("/source")))

    assert stream.stages == (
        CommandStage(tuple(option for option in _TAR_COMMAND if option != "--one-file-system")),
    )


def test_cap_export_runs_tar_on_source_target(tmp_path):
    target = SSHTarget("ssh://backup-source", tmp_path / "key")
    source = PathTree(Path("/source"), target)

    stream = TarDriver(tmp_path).cap_export(source)

    assert stream.stages == (CommandStage(_TAR_COMMAND, target),)


def test_cap_export_excludes_destination_inside_source():
    stream = TarDriver(Path("/source/archives")).cap_export(PathTree(Path("/source")))

    assert stream.stages == (
        CommandStage(
            (
                *_TAR_COMMAND[:-3],
                "--exclude=./archives",
                *_TAR_COMMAND[-3:],
            )
        ),
    )


def test_cap_export_excludes_remote_destination_on_same_endpoint(tmp_path):
    source_target = SSHTarget("ssh://host", tmp_path / "source-key")
    destination_target = SSHTarget("ssh://host", tmp_path / "destination-key")
    driver = TarDriver(Path("/source/archives"), destination_target)

    stream = driver.cap_export(PathTree(Path("/source"), source_target))

    expected = (
        *_TAR_COMMAND[:-3],
        "--exclude=./archives",
        *_TAR_COMMAND[-3:],
    )
    assert stream.stages == (CommandStage(expected, source_target),)


def test_cap_export_does_not_exclude_destination_on_different_endpoint(tmp_path):
    source_target = SSHTarget("ssh://source", tmp_path / "source-key")
    destination_target = SSHTarget("ssh://destination", tmp_path / "destination-key")
    driver = TarDriver(Path("/source/archives"), destination_target)

    stream = driver.cap_export(PathTree(Path("/source"), source_target))

    assert stream.stages == (CommandStage(_TAR_COMMAND, source_target),)


def test_cap_export_rejects_destination_equal_to_source(tmp_path):
    with pytest.raises(TarDriverError, match=f"destination is also the source: {tmp_path}"):
        TarDriver(tmp_path).cap_export(PathTree(tmp_path))


def test_capabilities_describe_tar_archive_lifecycle(tmp_path):
    driver = TarDriver(tmp_path)

    assert driver.capabilities() == {"export", "import", "list", "delete"}
    assert driver.capability_metadata("export").adds == {DataProperty.ARCHIVED}
    assert driver.capability_metadata("export").base is None
    assert driver.capability_metadata("import").requires == {DataProperty.ARCHIVED}
    assert driver.capability_metadata("import").base is None


def test_tar_representations(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")

    assert issubclass(TarStream, UncompressedStream)
    assert issubclass(TarArchive, Representation)
    assert TarStream.suffix == ".tar"
    assert TarArchive(tmp_path / "archive", target).ssh is target


def test_cap_import_stores_stream_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(tar_module, "uuid4", lambda: UUID(int=1))
    runner = RecordingRunner()
    driver = TarDriver(tmp_path)
    driver.runner = runner
    source = CommandStream(
        (CommandStage(("produce", "archive")),),
        suffixes=(".tar", ".zst", ".gpg"),
    )

    result = driver.cap_import(source, operation())

    artifact_name = f"{operation().artifact_name}.tar.zst.gpg"
    destination = tmp_path / artifact_name
    temporary = tmp_path / f".{artifact_name}.tmp-{UUID(int=1).hex}"
    assert result == BackupArtifact(operation(), TarArchive(destination))
    assert runner.pipeline_calls == [
        (
            (
                ("produce", "archive"),
                ("dd", f"of={temporary}", "bs=1048576"),
            ),
            False,
            True,
        )
    ]
    assert runner.run_calls == [
        (("mv", str(temporary), str(destination)), False, True),
    ]


def test_cap_import_writes_on_remote_target(tmp_path, monkeypatch):
    monkeypatch.setattr(tar_module, "uuid4", lambda: UUID(int=1))
    target = SSHTarget("ssh://host", tmp_path / "key")
    runner = RecordingRunner()
    driver = TarDriver(Path("/archives"), target)
    driver.runner = runner
    source = CommandStream((CommandStage(("produce", "archive")),))

    result = driver.cap_import(source, operation())

    destination = Path("/archives") / operation().artifact_name
    temporary = Path("/archives") / f".{operation().artifact_name}.tmp-{UUID(int=1).hex}"
    assert result.representation == TarArchive(destination, target)
    assert runner.pipeline_calls[0][0] == (
        ("produce", "archive"),
        target.openssh_command(("dd", f"of={temporary}", "bs=1048576")),
    )
    assert runner.run_calls[0][0] == target.openssh_command(("mv", temporary, destination))


def test_cap_import_removes_temporary_after_pipeline_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(tar_module, "uuid4", lambda: UUID(int=1))
    error = CommandError(("produce",), 1, "failed")
    runner = RecordingRunner(pipeline_failure=error)
    driver = TarDriver(tmp_path)
    driver.runner = runner

    with pytest.raises(CommandError) as raised:
        driver.cap_import(CommandStream((CommandStage(("produce",)),)), operation())

    assert raised.value is error
    temporary = tmp_path / f".{operation().artifact_name}.tmp-{UUID(int=1).hex}"
    assert runner.run_calls == [
        (("rm", "-f", str(temporary)), False, False),
    ]


def test_cap_import_removes_temporary_after_rename_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(tar_module, "uuid4", lambda: UUID(int=1))
    error = CommandError(("mv",), 1, "failed")
    runner = RecordingRunner(run_failures=(error, None))
    driver = TarDriver(tmp_path)
    driver.runner = runner

    with pytest.raises(CommandError) as raised:
        driver.cap_import(CommandStream((CommandStage(("produce",)),)), operation())

    assert raised.value is error
    assert runner.run_calls[-1][0][0:2] == ("rm", "-f")
    assert runner.run_calls[-1][2] is False


def test_cap_list_returns_newest_archives_first(tmp_path):
    old = tmp_path / f"{operation().artifact_name}.tar"
    new = tmp_path / f"{operation(1).artifact_name}.tar.zst.gpg"
    runner = RecordingRunner(stdouts=(f"{old}\n{tmp_path / 'unrelated'}\n{new}\n",))
    driver = TarDriver(tmp_path)
    driver.runner = runner

    artifacts = driver.cap_list("example")

    assert tuple(item.representation.path for item in artifacts) == (new, old)
    assert runner.run_calls == [
        (
            (
                "find",
                str(tmp_path),
                "!",
                "-path",
                str(tmp_path),
                "-prune",
                "-type",
                "f",
                "-print",
            ),
            True,
            True,
        )
    ]


def test_cap_list_runs_remotely(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")
    runner = RecordingRunner(stdouts=("",))
    driver = TarDriver(Path("/archives"), target)
    driver.runner = runner

    assert driver.cap_list("example") == ()
    assert runner.run_calls[0][0] == target.openssh_command(
        (
            "find",
            Path("/archives"),
            "!",
            "-path",
            Path("/archives"),
            "-prune",
            "-type",
            "f",
            "-print",
        )
    )


def test_format_locator(tmp_path):
    local = artifact(tmp_path / "local")
    target = SSHTarget("ssh://host", tmp_path / "key")
    remote = artifact(Path("/remote"), target)
    driver = TarDriver(tmp_path)

    assert driver.format_locator(local) == str(tmp_path / "local")
    assert driver.format_locator(remote) == f"{target}/remote"


def test_cap_delete_batches_archives(tmp_path):
    runner = RecordingRunner()
    driver = TarDriver(tmp_path)
    driver.runner = runner
    artifacts = (artifact(tmp_path / "one"), artifact(tmp_path / "two"))

    driver.cap_delete(artifacts)

    assert runner.run_calls == [
        (("rm", "-f", str(tmp_path / "one"), str(tmp_path / "two")), False, True),
    ]


def test_cap_delete_does_nothing_without_archives(tmp_path):
    runner = RecordingRunner()
    driver = TarDriver(tmp_path)
    driver.runner = runner

    driver.cap_delete(())

    assert runner.run_calls == []


def test_cap_delete_rejects_different_endpoint(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")
    driver = TarDriver(tmp_path)

    with pytest.raises(TarDriverError, match="different SSH endpoint"):
        driver.cap_delete((artifact(Path("/archive"), target),))


def test_encrypted_tar_pipeline_uses_tar_as_destination(tmp_path):
    source = RsyncDriver(Path("/source"))
    tar = TarDriver(Path("/archives"))
    gpg = GPGDriver(tmp_path / "public-key.asc")

    pipeline = Pipeline(
        source,
        tar,
        (gpg,),
    )

    assert pipeline.steps == (
        PipelineStep(source, "source"),
        PipelineStep(tar, "export"),
        PipelineStep(gpg, "encrypt"),
        PipelineStep(tar, "import"),
    )


def test_archive_pipeline_stores_suffixes_in_driver_order(tmp_path):
    tar = TarDriver(tmp_path)
    tar.runner = RecordingRunner()
    pipeline = Pipeline(
        RsyncDriver(Path("/source")),
        tar,
        (ZstdDriver(), GPGDriver(tmp_path / "public-key.asc")),
    )

    result = pipeline.execute(operation())

    assert result.representation.path == tmp_path / (f"{operation().artifact_name}.tar.zst.gpg")


def test_tar_creates_and_stores_archive(tmp_path):
    if shutil.which("tar") is None or shutil.which("dd") is None:
        pytest.skip("tar and dd are required")
    source = tmp_path / "source"
    destination = tmp_path / "archives"
    (source / "directory").mkdir(parents=True)
    destination.mkdir()
    (source / "directory" / "content").write_text("backup content")
    driver = TarDriver(destination)

    result = driver.cap_import(driver.cap_export(PathTree(source)), operation())

    archive = result.representation.path
    listing = CommandRunner().run(
        ("tar", "--list", f"--file={archive}"),
        capture_output=True,
    )
    assert set((listing.stdout or "").splitlines()) == {
        "./",
        "./directory/",
        "./directory/content",
    }
    assert driver.cap_list("example") == (result,)


def test_tar_does_not_archive_its_destination(tmp_path):
    source = tmp_path / "source"
    destination = source / "archives"
    source.mkdir()
    destination.mkdir()
    (source / "content").write_text("backup content")
    (destination / "old-archive").write_text("must not be archived")
    driver = TarDriver(destination)

    result = driver.cap_import(driver.cap_export(PathTree(source)), operation())

    listing = CommandRunner().run(
        ("tar", "--list", f"--file={result.representation.path}"),
        capture_output=True,
    )
    assert set((listing.stdout or "").splitlines()) == {"./", "./content"}


@pytest.mark.parametrize("executable", ["tar", "bsdtar"])
def test_tar_options_work_with_gnu_tar_and_bsdtar(tmp_path, executable):
    if shutil.which(executable) is None:
        pytest.skip(f"{executable} is required")
    source = tmp_path / "source"
    destination = source / "archives[1]*?"
    decoy = source / "archives1fooX"
    destination.mkdir(parents=True)
    decoy.mkdir()
    (source / "content").write_text("backup content")
    (destination / "excluded").write_text("must not be archived")
    (decoy / "included").write_text("must be archived")
    command = TarDriver(destination).cap_export(PathTree(source)).stages[0].command
    archive = tmp_path / f"{executable}.tar"

    CommandRunner().pipeline(
        (
            (executable, *command[1:]),
            ("dd", f"of={archive}", "bs=1048576"),
        )
    )

    listing = CommandRunner().run(
        (executable, "-t", "-f", archive),
        capture_output=True,
    )
    assert set((listing.stdout or "").splitlines()) == {
        "./",
        "./content",
        "./archives1fooX/",
        "./archives1fooX/included",
    }
