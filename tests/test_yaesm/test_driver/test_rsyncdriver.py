"""Tests for yaesm.driver.rsyncdriver."""

import hashlib
import shlex
import shutil
from datetime import datetime, timedelta

import pytest
import voluptuous as vlp

import yaesm.command as command_module
import yaesm.ty as ty
from yaesm.backup import Backup, BackupArtifact, BackupOperation
from yaesm.check import CheckRole
from yaesm.command import Command, CommandResult, CommandRunner
from yaesm.driver.btrfsdriver import BtrfsDriver, BtrfsSubvolume
from yaesm.driver.rsyncdriver import RsyncDriver, RsyncDriverError, RsyncTree
from yaesm.errors import YaesmValueError
from yaesm.pipeline import Pipeline, PipelineStep
from yaesm.representation import PathTree, ReadableTree
from yaesm.ssh import SSHTarget

_RSYNC_OPTIONS = (
    "rsync",
    "--archive",
    "--numeric-ids",
    "--delete",
    "--protect-args",
)
_MARKER_PREFIX = ".yaesm-rsync-artifact-"


def marker(path: ty.Path) -> ty.Path:
    digest = hashlib.sha256(path.name.encode()).hexdigest()
    return path.with_name(f"{_MARKER_PREFIX}{digest}")


class RecordingRunner(CommandRunner):
    def __init__(
        self,
        failures: ty.Iterable[BaseException | None] = (),
        stdouts: ty.Iterable[str | None] = (),
    ) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.failures = list(failures)
        self.stdouts = list(stdouts)

    def run(
        self,
        command: Command,
        *,
        capture_output: bool = False,
        check: bool = True,
    ) -> CommandResult:
        self.commands.append(tuple(str(argument) for argument in command))
        if self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                raise failure
        stdout = self.stdouts.pop(0) if self.stdouts else None
        return CommandResult(stdout, "", (0,))


def with_runner(driver: RsyncDriver, runner: RecordingRunner) -> RsyncDriver:
    driver.runner = runner
    return driver


def operation(offset: int = 0) -> BackupOperation:
    return BackupOperation(
        "example",
        "manual",
        datetime(2026, 8, 27, 12, 30) + timedelta(minutes=offset),
    )


def replicated_operation() -> BackupOperation:
    return BackupOperation(
        "example",
        "manual",
        datetime(2026, 8, 27, 12, 30),
        "yaesm-local-hourly.2026_08_27_12:30",
    )


def test_name():
    assert RsyncDriver.name() == "rsync"


def test_config_schema_defaults(tmp_path):
    assert RsyncDriver.config_schema()({"location": str(tmp_path)}) == {
        "location": tmp_path,
        "extra_options": (),
    }


def test_config_schema_accepts_path_location(tmp_path):
    assert RsyncDriver.config_schema()({"location": tmp_path})["location"] == tmp_path


def test_config_schema_accepts_ssh_target(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")

    assert RsyncDriver.config_schema()({"location": tmp_path, "target": target}) == {
        "location": tmp_path,
        "target": target,
        "extra_options": (),
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("--one-file-system", ("--one-file-system",)),
        ("--exclude='a b' --checksum", ("--exclude=a b", "--checksum")),
        (["--exclude cache", "--checksum"], ("--exclude", "cache", "--checksum")),
        ([], ()),
    ],
)
def test_config_schema_parses_extra_options(tmp_path, value, expected):
    assert (
        RsyncDriver.config_schema()({"location": tmp_path, "extra_options": value})["extra_options"]
        == expected
    )


@pytest.mark.parametrize("location", [None, 42])
def test_config_schema_rejects_invalid_location_type(location):
    with pytest.raises(vlp.Invalid, match="location must be a path"):
        RsyncDriver.config_schema()({"location": location})


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"location": "relative"},
        {"location": "/tmp", "target": None},
        {"location": "/tmp", "target": "ssh://host"},
        {"location": "/tmp", "extra_options": None},
        {"location": "/tmp", "extra_options": 1},
        {"location": "/tmp", "extra_options": True},
        {"location": "/tmp", "extra_options": ["--archive", 1]},
        {"location": "/tmp", "unknown": True},
    ],
)
def test_config_schema_rejects_invalid_config(config):
    with pytest.raises(vlp.Invalid):
        RsyncDriver.config_schema()(config)


def test_config_schema_rejects_malformed_extra_options(tmp_path):
    with pytest.raises(vlp.Invalid, match="invalid extra_options"):
        RsyncDriver.config_schema()({"location": tmp_path, "extra_options": "'"})


def test_config_schema_output_constructs_driver(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")
    config = RsyncDriver.config_schema()(
        {
            "location": tmp_path,
            "target": target,
            "extra_options": ["--checksum"],
        }
    )

    driver = RsyncDriver(**config)

    assert driver.location == tmp_path
    assert driver.target is target
    assert driver.extra_options == ("--checksum",)


@pytest.mark.parametrize("extra_options", ["--checksum", ("",), (1,)])
def test_constructor_rejects_invalid_extra_options(tmp_path, extra_options):
    with pytest.raises(YaesmValueError, match="must contain nonempty strings"):
        RsyncDriver(tmp_path, extra_options=ty.cast(ty.Any, extra_options))


def test_cap_source(tmp_path):
    driver = RsyncDriver(tmp_path)

    assert driver.capabilities() == {"source", "store", "list", "delete"}
    assert driver.capability_metadata("store").base == "destination"
    assert driver.cap_source() == PathTree(tmp_path)


def test_cap_source_includes_ssh_target(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")

    assert RsyncDriver(tmp_path, target).cap_source() == PathTree(tmp_path, target)


def test_source_checks_directory_requirements(tmp_path, monkeypatch):
    runner = RecordingRunner()
    monkeypatch.setattr(command_module, "run", runner.run)
    driver = RsyncDriver(tmp_path)

    checks = driver.check(CheckRole.SOURCE)

    assert tuple(check.description for check in checks) == (
        "rsync is installed",
        f"directory exists: {tmp_path}",
        f"directory is readable: {tmp_path}",
        f"directory is searchable: {tmp_path}",
    )
    assert runner.commands == []
    assert all(check.run().passed for check in checks)
    assert runner.commands == [
        ("rsync", "--version"),
        ("test", "-d", str(tmp_path)),
        ("test", "-r", str(tmp_path)),
        ("test", "-x", str(tmp_path)),
    ]


def test_destination_checks_directory_requirements_remotely(tmp_path, monkeypatch):
    target = SSHTarget("ssh://host", tmp_path / "key")
    runner = RecordingRunner()
    monkeypatch.setattr(command_module, "run", runner.run)
    driver = RsyncDriver(tmp_path, target)

    checks = driver.check(CheckRole.DESTINATION)
    for check in checks:
        check.run()

    assert tuple(check.description for check in checks) == (
        f"rsync is installed on {target}",
        f"directory exists: {tmp_path} on {target}",
        f"directory is readable: {tmp_path} on {target}",
        f"directory is writable: {tmp_path} on {target}",
        f"directory is searchable: {tmp_path} on {target}",
    )
    assert runner.commands == [
        target.openssh_command(("rsync", "--version")),
        target.openssh_command(("test", "-d", tmp_path)),
        target.openssh_command(("test", "-r", tmp_path)),
        target.openssh_command(("test", "-w", tmp_path)),
        target.openssh_command(("test", "-x", tmp_path)),
    ]


def test_artifact_source_checks_storage_read_requirements(tmp_path):
    checks = RsyncDriver(tmp_path)._checks(CheckRole.ARTIFACT_SOURCE)

    assert tuple(check.description for check in checks) == (
        f"directory exists: {tmp_path}",
        f"directory is readable: {tmp_path}",
        f"directory is searchable: {tmp_path}",
    )


@pytest.mark.parametrize(
    ("role", "index"),
    [
        (CheckRole.SOURCE, 0),
        (CheckRole.SOURCE, 1),
        (CheckRole.SOURCE, 2),
        (CheckRole.ARTIFACT_SOURCE, 0),
        (CheckRole.ARTIFACT_SOURCE, 1),
        (CheckRole.ARTIFACT_SOURCE, 2),
        (CheckRole.DESTINATION, 0),
        (CheckRole.DESTINATION, 1),
        (CheckRole.DESTINATION, 2),
        (CheckRole.DESTINATION, 3),
    ],
)
def test_each_directory_check_reports_failure(role, index, tmp_path, monkeypatch):
    monkeypatch.setattr(
        command_module,
        "run",
        lambda *args, **kwargs: CommandResult(None, "permission denied", (6,)),
    )
    check = RsyncDriver(tmp_path)._checks(role)[index]

    result = check.run()

    assert result.description == check.description
    assert result.passed is False
    assert result.failure == "test exited with status 6"
    assert result.stderr == "permission denied"


def test_transform_check_does_not_validate_unused_directory(tmp_path):
    driver = RsyncDriver(tmp_path)
    checks = driver.check(CheckRole.TRANSFORM)

    assert tuple(check.description for check in checks) == ("rsync is installed",)
    assert driver._checks(CheckRole.TRANSFORM) == ()


def test_cap_store_local(tmp_path):
    runner = RecordingRunner()
    source = PathTree(tmp_path / "source")
    destination_dir = tmp_path / "destination"
    driver = with_runner(RsyncDriver(destination_dir, extra_options=("--checksum",)), runner)

    artifact = driver.cap_store(source, operation())

    destination = destination_dir / operation().artifact_name
    assert artifact == BackupArtifact(operation(), RsyncTree(destination))
    assert runner.commands == [
        (*_RSYNC_OPTIONS, "--checksum", f"{source.path}/", f"{destination}/"),
        ("touch", str(marker(destination))),
    ]


def test_cap_store_marks_replicated_artifact(tmp_path):
    runner = RecordingRunner()
    source = PathTree(tmp_path / "source")
    destination_dir = tmp_path / "destination"
    operation_ = replicated_operation()

    artifact = with_runner(RsyncDriver(destination_dir), runner).cap_store(source, operation_)

    destination = destination_dir / operation_.artifact_name
    assert artifact == BackupArtifact(operation_, RsyncTree(destination))
    assert runner.commands == [
        (*_RSYNC_OPTIONS, f"{source.path}/", f"{destination}/"),
        ("touch", str(marker(destination))),
    ]


def test_cap_store_root_source_has_one_trailing_slash(tmp_path):
    runner = RecordingRunner()

    with_runner(RsyncDriver(tmp_path), runner).cap_store(PathTree(ty.Path("/")), operation())

    assert runner.commands[0][-2] == "/"


def test_cap_store_uses_link_dest(tmp_path):
    runner = RecordingRunner()
    source = PathTree(tmp_path / "source")
    base = RsyncTree(tmp_path / "destination" / "base")
    driver = with_runner(RsyncDriver(tmp_path / "destination"), runner)

    driver.cap_store(source, operation(), base)

    assert f"--link-dest={base.path}" in runner.commands[0]


def test_cap_store_rejects_base_on_different_endpoint(tmp_path):
    target = SSHTarget("ssh://destination", tmp_path / "key")
    base = RsyncTree(tmp_path / "base", SSHTarget("ssh://base", tmp_path / "key"))

    with pytest.raises(RsyncDriverError, match="base and destination use different"):
        RsyncDriver(tmp_path, target).cap_store(PathTree(tmp_path / "source"), operation(), base)


def test_incremental_base_requires_rsync_tree_on_destination_endpoint(tmp_path):
    target = SSHTarget("ssh://destination", tmp_path / "key")
    driver = RsyncDriver(tmp_path, target)
    source = PathTree(tmp_path / "source")
    base = RsyncTree(tmp_path / "base", target)

    assert driver.validate_base("store", source, None, base)
    assert not driver.validate_base("store", source, None, PathTree(base.path, target))
    assert not driver.validate_base(
        "store",
        source,
        None,
        RsyncTree(base.path, SSHTarget("ssh://other", tmp_path / "key")),
    )
    assert not driver.validate_base("export", source, None, base)


def test_cap_store_local_to_remote(tmp_path):
    runner = RecordingRunner()
    target = SSHTarget("ssh://user@host:2222", tmp_path / "key")
    source = PathTree(tmp_path / "source")
    destination_dir = tmp_path / "destination"

    with_runner(RsyncDriver(destination_dir, target), runner).cap_store(source, operation())

    destination = destination_dir / operation().artifact_name
    assert runner.commands == [
        (
            *_RSYNC_OPTIONS,
            f"--rsh={shlex.join(('ssh', *target.openssh_options()))}",
            f"{source.path}/",
            f"user@host:{destination}/",
        ),
        target.openssh_command(("touch", marker(destination))),
    ]


def test_cap_store_remote_to_local(tmp_path):
    runner = RecordingRunner()
    target = SSHTarget("ssh://user@host", tmp_path / "key")
    source = PathTree(tmp_path / "source", target)
    destination_dir = tmp_path / "destination"

    with_runner(RsyncDriver(destination_dir), runner).cap_store(source, operation())

    destination = destination_dir / operation().artifact_name
    assert runner.commands == [
        (
            *_RSYNC_OPTIONS,
            f"--rsh={shlex.join(('ssh', *target.openssh_options()))}",
            f"user@host:{source.path}/",
            f"{destination}/",
        ),
        ("touch", str(marker(destination))),
    ]


def test_cap_store_remote_ipv6(tmp_path):
    runner = RecordingRunner()
    target = SSHTarget("ssh://user@[2001:db8::1]", tmp_path / "key")
    source = PathTree(tmp_path / "source", target)

    with_runner(RsyncDriver(tmp_path), runner).cap_store(source, operation())

    assert runner.commands[0][-2] == f"user@[2001:db8::1]:{source.path}/"


def test_cap_store_on_same_remote_endpoint(tmp_path):
    runner = RecordingRunner()
    source_target = SSHTarget("ssh://host", tmp_path / "source-key")
    destination_target = SSHTarget("ssh://host", tmp_path / "destination-key")
    source = PathTree(tmp_path / "source", source_target)
    destination_dir = tmp_path / "destination"

    with_runner(RsyncDriver(destination_dir, destination_target), runner).cap_store(
        source,
        operation(),
    )

    destination = destination_dir / operation().artifact_name
    assert runner.commands == [
        destination_target.openssh_command((*_RSYNC_OPTIONS, f"{source.path}/", f"{destination}/")),
        destination_target.openssh_command(("touch", marker(destination))),
    ]


def test_cap_store_rejects_different_remote_endpoints(tmp_path):
    runner = RecordingRunner()
    source = PathTree(
        tmp_path / "source",
        SSHTarget("ssh://source", tmp_path / "source-key"),
    )
    destination = SSHTarget("ssh://destination", tmp_path / "destination-key")

    with pytest.raises(RsyncDriverError, match="cannot copy between different SSH endpoints"):
        with_runner(RsyncDriver(tmp_path, destination), runner).cap_store(source, operation())

    assert runner.commands == []


def test_cap_store_cleans_up_failure(tmp_path):
    runner = RecordingRunner((RuntimeError("rsync failed"), None))
    source = PathTree(tmp_path / "source")
    destination_dir = tmp_path / "destination"

    with pytest.raises(RuntimeError, match="rsync failed"):
        with_runner(RsyncDriver(destination_dir), runner).cap_store(source, operation())

    destination = destination_dir / operation().artifact_name
    assert runner.commands[-1] == (
        "rm",
        "-rf",
        str(destination),
        str(marker(destination)),
    )


def test_cap_store_cleans_up_marker_failure(tmp_path):
    runner = RecordingRunner((None, RuntimeError("marker failed"), None))
    source = PathTree(tmp_path / "source")
    destination_dir = tmp_path / "destination"

    with pytest.raises(RuntimeError, match="marker failed"):
        with_runner(RsyncDriver(destination_dir), runner).cap_store(source, operation())

    destination = destination_dir / operation().artifact_name
    assert runner.commands[-1] == (
        "rm",
        "-rf",
        str(destination),
        str(marker(destination)),
    )


def test_cap_list_returns_matching_artifacts_newest_first(tmp_path):
    destination = tmp_path / "destination"
    older = operation()
    newer = BackupOperation(
        "example",
        "manual",
        datetime(2026, 8, 27, 12, 31),
    )
    unmarked = operation(2)
    runner = RecordingRunner(
        stdouts=(
            "\n".join(
                (
                    str(destination / older.artifact_name),
                    str(destination / "unrelated"),
                    str(destination / "yaesm-other-manual.2026_08_27_12:32"),
                    str(destination / newer.artifact_name),
                    str(destination / unmarked.artifact_name),
                    str(marker(destination / older.artifact_name)),
                    str(marker(destination / newer.artifact_name)),
                    str(marker(destination / "missing")),
                )
            ),
        )
    )

    artifacts = with_runner(RsyncDriver(destination), runner).cap_list("example")

    assert artifacts == (
        BackupArtifact(newer, RsyncTree(destination / newer.artifact_name)),
        BackupArtifact(older, RsyncTree(destination / older.artifact_name)),
    )
    assert runner.commands == [
        (
            "find",
            str(destination),
            "!",
            "-path",
            str(destination),
            "-prune",
            "(",
            "-type",
            "d",
            "-o",
            "-type",
            "f",
            "-name",
            f"{_MARKER_PREFIX}*",
            ")",
            "-print",
        ),
    ]


def test_cap_list_remote(tmp_path):
    runner = RecordingRunner()
    target = SSHTarget("ssh://host", tmp_path / "key")
    destination = tmp_path / "destination"

    assert with_runner(RsyncDriver(destination, target), runner).cap_list("example") == ()
    assert runner.commands == [
        target.openssh_command(
            (
                "find",
                destination,
                "!",
                "-path",
                destination,
                "-prune",
                "(",
                "-type",
                "d",
                "-o",
                "-type",
                "f",
                "-name",
                f"{_MARKER_PREFIX}*",
                ")",
                "-print",
            )
        ),
    ]


def test_formats_local_and_remote_artifact_locators(tmp_path):
    operation_ = operation()
    path = tmp_path / operation_.artifact_name
    target = SSHTarget("ssh://host", tmp_path / "key")
    driver = RsyncDriver(tmp_path)

    assert driver.format_locator(BackupArtifact(operation_, RsyncTree(path))) == str(path)
    assert driver.format_locator(
        BackupArtifact(operation_, RsyncTree(path, target))
    ) == target.format_location(path)


def test_cap_delete_batches_artifacts(tmp_path):
    runner = RecordingRunner()
    artifacts = (
        BackupArtifact(operation(), RsyncTree(tmp_path / "one")),
        BackupArtifact(operation(), RsyncTree(tmp_path / "two")),
    )

    with_runner(RsyncDriver(tmp_path), runner).cap_delete(artifacts)

    assert runner.commands == [
        (
            "rm",
            "-rf",
            str(tmp_path / "one"),
            str(marker(tmp_path / "one")),
            str(tmp_path / "two"),
            str(marker(tmp_path / "two")),
        )
    ]


def test_cap_delete_batches_remote_artifacts(tmp_path):
    runner = RecordingRunner()
    target = SSHTarget("ssh://host", tmp_path / "key")
    artifacts = (
        BackupArtifact(operation(), RsyncTree(tmp_path / "one", target)),
        BackupArtifact(operation(), RsyncTree(tmp_path / "two", target)),
    )

    with_runner(RsyncDriver(tmp_path, target), runner).cap_delete(artifacts)

    assert runner.commands == [
        target.openssh_command(
            (
                "rm",
                "-rf",
                tmp_path / "one",
                marker(tmp_path / "one"),
                tmp_path / "two",
                marker(tmp_path / "two"),
            )
        )
    ]


def test_cap_delete_accepts_empty_sequence(tmp_path):
    runner = RecordingRunner()

    with_runner(RsyncDriver(tmp_path), runner).cap_delete(())

    assert runner.commands == []


def test_cap_delete_rejects_different_endpoint(tmp_path):
    artifact_target = SSHTarget("ssh://artifact", tmp_path / "key")
    driver_target = SSHTarget("ssh://driver", tmp_path / "key")
    artifact = BackupArtifact(operation(), RsyncTree(tmp_path / "snapshot", artifact_target))

    with pytest.raises(RsyncDriverError, match="different SSH endpoint"):
        RsyncDriver(tmp_path, driver_target).cap_delete((artifact,))


def test_pipeline_uses_rsync_store(tmp_path):
    source = RsyncDriver(tmp_path / "source")
    destination = RsyncDriver(tmp_path / "destination")

    assert Pipeline(source, destination).steps == (
        PipelineStep(source, "source"),
        PipelineStep(destination, "store"),
    )


def test_pipeline_snapshots_btrfs_tree_before_storing_with_rsync(tmp_path):
    source = BtrfsDriver(tmp_path / "source")
    destination = RsyncDriver(tmp_path / "destination")

    assert Pipeline(source, destination).steps == (
        PipelineStep(source, "source"),
        PipelineStep(source, "snapshot"),
        PipelineStep(destination, "store"),
    )
    assert issubclass(BtrfsSubvolume, PathTree)


def test_rsync_representation_types():
    assert issubclass(RsyncTree, PathTree)
    assert issubclass(RsyncTree, ReadableTree)


def test_rsync_integration(tmp_path):
    if shutil.which("rsync") is None:
        pytest.skip("rsync is not installed")

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "unchanged").write_text("same")
    (source / "changed").write_text("before")
    source_driver = RsyncDriver(source)
    driver = RsyncDriver(destination)
    backup = Backup("example", source_driver, driver)

    first = backup.execute("manual", operation().created_at)
    (source / "changed").write_text("after")
    second = backup.execute("manual", operation(1).created_at)

    assert (second.representation.path / "changed").read_text() == "after"
    assert (first.representation.path / "unchanged").stat().st_ino == (
        second.representation.path / "unchanged"
    ).stat().st_ino
    assert driver.cap_list("example") == (second, first)

    driver.cap_delete((first, second))
    assert not first.representation.path.exists()
    assert not second.representation.path.exists()
    assert not any(destination.iterdir())
