"""Tests for yaesm.driver.rsyncdriver."""

import shlex
import shutil
from datetime import datetime, timedelta

import pytest
import voluptuous as vlp

import yaesm.command as command_module
import yaesm.ty as ty
from yaesm.backup import Backup, BackupArtifact, BackupOperation, DriverSource
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


def test_transform_check_does_not_validate_unused_directory(tmp_path):
    checks = RsyncDriver(tmp_path).check(CheckRole.TRANSFORM)

    assert tuple(check.description for check in checks) == ("rsync is installed",)


def test_cap_store_local(tmp_path):
    runner = RecordingRunner()
    source = PathTree(tmp_path / "source")
    destination_dir = tmp_path / "destination"
    driver = with_runner(RsyncDriver(destination_dir, extra_options=("--checksum",)), runner)

    artifact = driver.cap_store(source, operation())

    destination = destination_dir / operation().artifact_name
    assert artifact == BackupArtifact(operation(), RsyncTree(destination))
    assert runner.commands == [
        (*_RSYNC_OPTIONS, "--checksum", f"{source.path}/", f"{destination}/")
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
        )
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
        )
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
        destination_target.openssh_command((*_RSYNC_OPTIONS, f"{source.path}/", f"{destination}/"))
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
    assert runner.commands[-1] == ("rm", "-rf", str(destination))


def test_cap_list_returns_matching_artifacts_newest_first(tmp_path):
    destination = tmp_path / "destination"
    older = operation()
    newer = operation(1)
    runner = RecordingRunner(
        stdouts=(
            "\n".join(
                (
                    str(destination / older.artifact_name),
                    str(destination / "unrelated"),
                    str(destination / "yaesm-other-manual.2026_08_27_12:32"),
                    str(destination / newer.artifact_name),
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
            "-type",
            "d",
            "-print",
        )
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
                "-type",
                "d",
                "-print",
            )
        )
    ]


def test_cap_delete_batches_artifacts(tmp_path):
    runner = RecordingRunner()
    artifacts = (
        BackupArtifact(operation(), RsyncTree(tmp_path / "one")),
        BackupArtifact(operation(), RsyncTree(tmp_path / "two")),
    )

    with_runner(RsyncDriver(tmp_path), runner).cap_delete(artifacts)

    assert runner.commands == [("rm", "-rf", str(tmp_path / "one"), str(tmp_path / "two"))]


def test_cap_delete_batches_remote_artifacts(tmp_path):
    runner = RecordingRunner()
    target = SSHTarget("ssh://host", tmp_path / "key")
    artifacts = (
        BackupArtifact(operation(), RsyncTree(tmp_path / "one", target)),
        BackupArtifact(operation(), RsyncTree(tmp_path / "two", target)),
    )

    with_runner(RsyncDriver(tmp_path, target), runner).cap_delete(artifacts)

    assert runner.commands == [
        target.openssh_command(("rm", "-rf", tmp_path / "one", tmp_path / "two"))
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

    assert Pipeline(DriverSource(source), destination).steps == (
        PipelineStep(source, "source"),
        PipelineStep(destination, "store"),
    )


def test_pipeline_can_store_btrfs_tree_with_rsync(tmp_path):
    source = BtrfsDriver(tmp_path / "source")
    destination = RsyncDriver(tmp_path / "destination")

    assert Pipeline(DriverSource(source), destination).steps == (
        PipelineStep(source, "source"),
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
    backup = Backup("example", DriverSource(source_driver), driver)

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
