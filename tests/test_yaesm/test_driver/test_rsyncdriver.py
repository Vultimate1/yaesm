"""Tests for yaesm.driver.rsyncdriver."""

import dataclasses
import os
import shlex
import shutil
import struct
from datetime import datetime, timedelta
from uuid import UUID

import pytest
import voluptuous as vlp

import yaesm.command as command_module
import yaesm.driver.rsyncdriver as rsync_module
import yaesm.ty as ty
from yaesm.backup import Backup, BackupArtifact, BackupOperation, BackupSource
from yaesm.check import CheckRole
from yaesm.command import Command, CommandResult, CommandRunner
from yaesm.driver.btrfsdriver import BtrfsDriver, BtrfsSubvolume
from yaesm.driver.directorydriver import DirectoryDriver
from yaesm.driver.rsyncdriver import RsyncDriver, RsyncDriverError, RsyncTree
from yaesm.errors import YaesmValueError
from yaesm.pipeline import Pipeline, PipelineStep
from yaesm.representation import PathTree, ReadableTree
from yaesm.ssh import SSHTarget
from yaesm.subcommand.checksubcommand import CheckSubcommand
from yaesm.xattr import XAttr

_RSYNC_OPTIONS = (
    "rsync",
    "--archive",
    "--hard-links",
    "--acls",
    "--xattrs",
    "--sparse",
    "--numeric-ids",
    "--delete",
    "--delete-excluded",
    "-s",
    "--filter=-x user.yaesm.*",
    "--filter=-x yaesm.*",
    "--filter=-xsr system.*",
)


@pytest.fixture(autouse=True)
def fixed_uuid(monkeypatch):
    monkeypatch.setattr(rsync_module, "uuid4", lambda: UUID(int=1))


def temporary(path: ty.Path) -> ty.Path:
    return path.with_name(f".{path.name}.tmp-{UUID(int=1).hex}")


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


class ShellSSHRunner(CommandRunner):
    def run(self, command: Command, **options) -> CommandResult:
        if command[0] == "ssh":
            command = ("sh", "-c", command[-1])
        return super().run(command, **options)


def with_runner(driver: RsyncDriver, runner: CommandRunner) -> RsyncDriver:
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
        "yaesm.local.hourly.2026_08_27_12:30.p0000",
    )


def test_name():
    assert RsyncDriver.name() == "rsync"


def test_config_schema_defaults(tmp_path):
    assert RsyncDriver.config_schema()({"location": str(tmp_path)}) == {
        "location": tmp_path,
        "extra_options": (),
        "exclude": (),
        "one_file_system": False,
    }


def test_config_schema_accepts_path_location(tmp_path):
    assert RsyncDriver.config_schema()({"location": tmp_path})["location"] == tmp_path


def test_config_schema_accepts_shorthand(tmp_path):
    assert RsyncDriver.config_schema()(tmp_path) == {
        "location": tmp_path,
        "extra_options": (),
        "exclude": (),
        "one_file_system": False,
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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (".cache/", (".cache/",)),
        ([".cache/", "*.tmp"], (".cache/", "*.tmp")),
        ((), ()),
    ],
)
def test_config_schema_parses_exclude(tmp_path, value, expected):
    assert (
        RsyncDriver.config_schema()({"location": tmp_path, "exclude": value})["exclude"] == expected
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
        {"location": "/tmp", "ssh": None},
        {"location": "/tmp", "ssh": "ssh://host"},
        {"location": "/tmp", "extra_options": None},
        {"location": "/tmp", "extra_options": 1},
        {"location": "/tmp", "extra_options": True},
        {"location": "/tmp", "extra_options": ["--archive", 1]},
        {"location": "/tmp", "exclude": None},
        {"location": "/tmp", "exclude": 1},
        {"location": "/tmp", "exclude": True},
        {"location": "/tmp", "exclude": [".cache/", ""]},
        {"location": "/tmp", "exclude": [".cache/", 1]},
        {"location": "/tmp", "one_file_system": None},
        {"location": "/tmp", "one_file_system": 1},
        {"location": "/tmp", "one_file_system": "true"},
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
    config = RsyncDriver.config_schema()(
        {
            "location": tmp_path,
            "extra_options": ["--checksum"],
            "exclude": [".cache/"],
            "one_file_system": True,
        }
    )

    driver = RsyncDriver(**config)

    assert driver.location == tmp_path
    assert driver.ssh is None
    assert driver.extra_options == ("--checksum",)
    assert driver.exclude == (".cache/",)
    assert driver.one_file_system is True


@pytest.mark.parametrize("extra_options", ["--checksum", ("",), (1,)])
def test_constructor_rejects_invalid_extra_options(tmp_path, extra_options):
    with pytest.raises(YaesmValueError, match="must contain nonempty strings"):
        RsyncDriver(tmp_path, extra_options=ty.cast(ty.Any, extra_options))


@pytest.mark.parametrize("exclude", [".cache/", ("",), (1,)])
def test_constructor_rejects_invalid_exclude(tmp_path, exclude):
    with pytest.raises(YaesmValueError, match="exclude must contain nonempty strings"):
        RsyncDriver(tmp_path, exclude=ty.cast(ty.Any, exclude))


@pytest.mark.parametrize("one_file_system", [None, 0, 1, "true"])
def test_constructor_rejects_invalid_one_file_system(tmp_path, one_file_system):
    with pytest.raises(YaesmValueError, match="one_file_system must be a boolean"):
        RsyncDriver(tmp_path, one_file_system=ty.cast(ty.Any, one_file_system))


def test_capabilities(tmp_path):
    driver = RsyncDriver(tmp_path)

    assert driver.capabilities() == {"store", "list", "delete"}
    assert driver.capability_metadata("store").base == "destination"


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


@pytest.mark.parametrize("role", [CheckRole.SOURCE, CheckRole.TRANSFORM])
def test_unused_roles_do_not_validate_directory(role, tmp_path):
    driver = RsyncDriver(tmp_path)
    checks = driver.check(role)

    assert tuple(check.description for check in checks) == ("rsync is installed",)
    assert driver._checks(role) == ()


def test_cap_store_local(tmp_path):
    runner = RecordingRunner()
    source = PathTree(tmp_path / "source")
    destination_dir = tmp_path / "destination"
    driver = with_runner(RsyncDriver(destination_dir, extra_options=("--checksum",)), runner)

    artifact = driver.cap_store(source, operation())

    destination = destination_dir / operation().artifact_name
    assert artifact == BackupArtifact(operation(), RsyncTree(destination))
    assert runner.commands == [
        ("mkdir", str(temporary(destination))),
        (*_RSYNC_OPTIONS, "--checksum", f"{source.path}/", f"{temporary(destination)}/"),
        ("mv", str(temporary(destination)), str(destination)),
    ]


def test_cap_store_uses_exclude_patterns(tmp_path):
    runner = RecordingRunner()
    source = PathTree(tmp_path / "source")
    destination_dir = tmp_path / "destination"
    driver = with_runner(
        RsyncDriver(destination_dir, exclude=(".cache/", "*.tmp")),
        runner,
    )

    driver.cap_store(source, operation())

    destination = destination_dir / operation().artifact_name
    assert runner.commands[-2] == (
        *_RSYNC_OPTIONS,
        "--exclude=.cache/",
        "--exclude=*.tmp",
        f"{source.path}/",
        f"{temporary(destination)}/",
    )


def test_cap_store_uses_protected_path_exclusions(tmp_path):
    runner = RecordingRunner()
    source_path = tmp_path / "source"
    source = PathTree(
        source_path,
        excluded_paths=(ty.Path("backups[1]*?"),),
    )
    destination_dir = tmp_path / "destination"
    driver = with_runner(RsyncDriver(destination_dir), runner)

    driver.cap_store(source, operation())

    assert "--exclude=/backups\\[1\\]\\*\\?/" in runner.commands[-2]


@pytest.mark.parametrize(
    "extra_options",
    [
        ("--filter=!",),
        ("--include-from=/filters",),
        ("--exclude-from=/filters",),
        ("-f", "- *.tmp"),
        ("-avf- *.tmp",),
    ],
)
def test_cap_store_rejects_custom_filters_when_paths_are_protected(
    tmp_path,
    extra_options,
):
    runner = RecordingRunner()
    source = PathTree(
        tmp_path / "source",
        excluded_paths=(ty.Path("backups"),),
    )
    driver = with_runner(RsyncDriver(tmp_path / "destination", extra_options=extra_options), runner)

    with pytest.raises(
        RsyncDriverError,
        match="extra_options could override required protected-path filters",
    ):
        driver.cap_store(source, operation())

    assert runner.commands == []


def test_cap_store_keeps_protected_exclusions_before_simple_extra_filters(tmp_path):
    runner = RecordingRunner()
    source = PathTree(
        tmp_path / "source",
        excluded_paths=(ty.Path("backups"),),
    )
    driver = with_runner(
        RsyncDriver(
            tmp_path / "destination",
            extra_options=("--include=/backups/***", "--exclude=*.tmp"),
        ),
        runner,
    )

    driver.cap_store(source, operation())

    command = runner.commands[-2]
    assert command.index("--exclude=/backups/") < command.index("--include=/backups/***")


def test_cap_store_can_stay_on_one_file_system(tmp_path):
    runner = RecordingRunner()
    source = PathTree(tmp_path / "source")
    destination_dir = tmp_path / "destination"
    driver = with_runner(RsyncDriver(destination_dir, one_file_system=True), runner)

    driver.cap_store(source, operation())

    assert runner.commands[-2].count("--one-file-system") == 1


def test_cap_store_stores_replica_identity_before_rename(tmp_path, monkeypatch):
    runner = RecordingRunner()
    source = PathTree(tmp_path / "source")
    driver = with_runner(RsyncDriver(tmp_path / "destination"), runner)
    operation_ = replicated_operation()

    def write(path, value):
        assert path == temporary(driver.location / operation_.artifact_name)
        assert value == operation_.source_artifact_id
        assert runner.commands[-1][0] == "rsync"
        return True

    monkeypatch.setattr(driver._source_id, "write", write)
    artifact = driver.cap_store(source, operation_)

    assert artifact == BackupArtifact(
        operation_, RsyncTree(driver.location / operation_.artifact_name)
    )
    assert runner.commands[-1] == (
        "mv",
        str(temporary(artifact.representation.path)),
        str(artifact.representation.path),
    )


def test_cap_store_root_source_has_one_trailing_slash(tmp_path):
    runner = RecordingRunner()

    with_runner(RsyncDriver(tmp_path), runner).cap_store(PathTree(ty.Path("/")), operation())

    assert runner.commands[-2][-2] == "/"


def test_cap_store_uses_link_dest(tmp_path):
    runner = RecordingRunner()
    source = PathTree(tmp_path / "source")
    base = RsyncTree(tmp_path / "destination" / "base")
    driver = with_runner(RsyncDriver(tmp_path / "destination"), runner)

    driver.cap_store(source, operation(), base)

    assert f"--link-dest={base.path}" in runner.commands[-2]


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
        target.openssh_command(("mkdir", temporary(destination))),
        (
            *_RSYNC_OPTIONS,
            f"--rsh={shlex.join(('ssh', *target.openssh_options()))}",
            f"{source.path}/",
            f"user@host:{temporary(destination)}/",
        ),
        target.openssh_command(("mv", temporary(destination), destination)),
    ]


def test_cap_store_remote_to_local(tmp_path):
    runner = RecordingRunner()
    target = SSHTarget("ssh://user@host", tmp_path / "key")
    source = PathTree(tmp_path / "source", target)
    destination_dir = tmp_path / "destination"

    with_runner(RsyncDriver(destination_dir), runner).cap_store(source, operation())

    destination = destination_dir / operation().artifact_name
    assert runner.commands == [
        ("mkdir", str(temporary(destination))),
        (
            *_RSYNC_OPTIONS,
            f"--rsh={shlex.join(('ssh', *target.openssh_options()))}",
            f"user@host:{source.path}/",
            f"{temporary(destination)}/",
        ),
        ("mv", str(temporary(destination)), str(destination)),
    ]


def test_cap_store_remote_ipv6(tmp_path):
    runner = RecordingRunner()
    target = SSHTarget("ssh://user@[2001:db8::1]", tmp_path / "key")
    source = PathTree(tmp_path / "source", target)

    with_runner(RsyncDriver(tmp_path), runner).cap_store(source, operation())

    assert runner.commands[-2][-2] == f"user@[2001:db8::1]:{source.path}/"


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
        destination_target.openssh_command(("mkdir", temporary(destination))),
        destination_target.openssh_command(
            (*_RSYNC_OPTIONS, f"{source.path}/", f"{temporary(destination)}/")
        ),
        destination_target.openssh_command(("mv", temporary(destination), destination)),
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
    runner = RecordingRunner((None, RuntimeError("rsync failed"), None))
    source = PathTree(tmp_path / "source")
    destination_dir = tmp_path / "destination"

    with pytest.raises(RuntimeError, match="rsync failed"):
        with_runner(RsyncDriver(destination_dir), runner).cap_store(source, operation())

    destination = destination_dir / operation().artifact_name
    assert runner.commands[-1] == (
        "rm",
        "-rf",
        str(temporary(destination)),
    )


def test_cap_store_cleans_up_rename_failure(tmp_path):
    runner = RecordingRunner((None, None, RuntimeError("rename failed"), None))
    source = PathTree(tmp_path / "source")
    destination_dir = tmp_path / "destination"

    with pytest.raises(RuntimeError, match="rename failed"):
        with_runner(RsyncDriver(destination_dir), runner).cap_store(source, operation())

    destination = destination_dir / operation().artifact_name
    assert runner.commands[-1] == (
        "rm",
        "-rf",
        str(temporary(destination)),
    )


@pytest.mark.parametrize("kind", ["file", "directory", "symlink"])
def test_cap_store_preserves_yaesm_named_source_entries(tmp_path, kind):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    entry = source / ".yaesm"
    if kind == "file":
        entry.write_text("user data")
    elif kind == "directory":
        entry.mkdir()
    else:
        entry.symlink_to(tmp_path / "missing")

    artifact = RsyncDriver(destination).cap_store(PathTree(source), operation())

    copied = artifact.representation.path / ".yaesm"
    if kind == "file":
        assert copied.read_text() == "user data"
    elif kind == "directory":
        assert copied.is_dir()
    else:
        assert copied.readlink() == entry.readlink()
    assert entry.exists() or entry.is_symlink()


def test_cap_list_returns_matching_artifacts_newest_first(tmp_path):
    destination = tmp_path / "destination"
    older = operation()
    newer = BackupOperation(
        "example",
        "manual",
        datetime(2026, 8, 27, 12, 31),
    )
    unfinished = operation(2)
    for name in (
        older.artifact_name,
        newer.artifact_name,
        temporary(destination / unfinished.artifact_name).name,
        "unrelated",
        "yaesm.other.manual.2026_08_27_12:32.p0000",
    ):
        (destination / name).mkdir(parents=True)

    artifacts = RsyncDriver(destination).cap_list("example")

    assert artifacts == (
        BackupArtifact(newer, RsyncTree(destination / newer.artifact_name)),
        BackupArtifact(older, RsyncTree(destination / older.artifact_name)),
    )


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
            str(tmp_path / "two"),
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
                tmp_path / "two",
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


def test_rsync_does_not_support_unchanged(tmp_path):
    assert "unchanged" not in RsyncDriver(tmp_path).capabilities()


def test_pipeline_uses_rsync_store(tmp_path):
    source = DirectoryDriver(tmp_path / "source")
    destination = RsyncDriver(tmp_path / "destination")

    assert Pipeline(source, destination).steps == (
        PipelineStep(source, "source"),
        PipelineStep(destination, "store"),
    )


def test_pipeline_excludes_nested_rsync_destination(tmp_path):
    source_path = tmp_path / "source"
    destination_path = source_path / "backups"
    runner = RecordingRunner()
    destination = with_runner(RsyncDriver(destination_path), runner)

    Pipeline(DirectoryDriver(source_path), destination).execute(operation())

    assert "--exclude=/backups/" in runner.commands[-2]


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
    source_driver = DirectoryDriver(source)
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


def test_rsync_preserves_access_and_default_acls(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "content").write_text("payload")
    # Linux ACL encoding: owner, named user, group, mask, other.
    acl = struct.pack("<I", 2) + b"".join(
        struct.pack("<HHI", tag, permissions, uid)
        for tag, permissions, uid in (
            (1, 7, 0xFFFFFFFF),
            (2, 5, 1234),
            (4, 5, 0xFFFFFFFF),
            (16, 5, 0xFFFFFFFF),
            (32, 0, 0xFFFFFFFF),
        )
    )
    for path in (source, source / "content"):
        os.setxattr(path, "system.posix_acl_access", acl)
    os.setxattr(source, "system.posix_acl_default", acl)

    driver = RsyncDriver(destination)
    base = None
    for offset in (0, 1):
        artifact = driver.cap_store(PathTree(source), operation(offset), base)
        base = artifact.representation
        for path in (base.path, base.path / "content"):
            assert os.getxattr(path, "system.posix_acl_access") == acl
        assert os.getxattr(base.path, "system.posix_acl_default") == acl


@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("initial_skip_unchanged", [False, True])
def test_rsync_replication_skips_unchanged_source_across_schedules(
    tmp_path, remote, initial_skip_unchanged
):
    if shutil.which("rsync") is None:
        pytest.skip("rsync is not installed")

    source = tmp_path / "source"
    local_path = tmp_path / "local"
    offsite_path = tmp_path / "offsite '$(false)"
    for path in (source, local_path, offsite_path):
        path.mkdir()
    (source / "content").write_text("first")
    target = SSHTarget("ssh://host", tmp_path / "key") if remote else None
    local = Backup(
        "local",
        DirectoryDriver(source, target),
        with_runner(RsyncDriver(local_path, target), ShellSSHRunner()),
    )
    assert XAttr("yaesm.source-artifact", CommandRunner()).write(source, "inherited-id")
    first_source = local.execute("hourly", operation().created_at)
    assert local.artifacts()[0].operation.source_artifact_id is None
    replica = Backup(
        "offsite",
        BackupSource("local"),
        with_runner(RsyncDriver(offsite_path, target), ShellSSHRunner()),
        skip_unchanged=initial_skip_unchanged,
    )
    first = replica.execute("daily", operation().created_at, {"local": local})

    # Reconstruct the backup to ensure the source identity comes from disk.
    replica = Backup(
        "offsite",
        BackupSource("local"),
        with_runner(RsyncDriver(offsite_path, target), ShellSSHRunner()),
        skip_unchanged=True,
    )
    assert replica.artifacts() == (first,)
    assert replica.execute("weekly", operation(1).created_at, {"local": local}) == first
    assert replica.artifacts() == (first,)

    # A different source artifact with the same timestamp must still be copied.
    local.destination.cap_delete((first_source,))
    (source / "content").write_text("second version")
    second_source = local.execute("manual", operation().created_at)
    second = replica.execute("weekly", operation(2).created_at, {"local": local})
    assert second.operation.source_artifact_id == local.destination.artifact_id(second_source)
    assert len(replica.artifacts()) == 2
    assert (first.representation.path / "content").read_text() == "first"
    assert (second.representation.path / "content").read_text() == "second version"
    assert RsyncDriver(offsite_path)._source_id.read(
        first.representation.path
    ) == local.destination.artifact_id(first_source)
    assert RsyncDriver(offsite_path)._source_id.read(
        second.representation.path
    ) == local.destination.artifact_id(second_source)

    assert tuple(path.name for path in second.representation.path.iterdir()) == ("content",)

    moved = tmp_path / "moved"
    shutil.copytree(first.representation.path, moved / first.name)
    assert RsyncDriver(moved).cap_list("offsite")[0].operation == first.operation


def test_rsync_metadata_preserves_source_identity_text(tmp_path):
    if shutil.which("rsync") is None:
        pytest.skip("rsync is not installed")
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    operation_ = BackupOperation(
        "example", "manual", operation().created_at, "source '$(false) %s\n\n"
    )
    driver = RsyncDriver(destination)

    artifact = driver.cap_store(PathTree(source), operation_)

    assert driver._source_id.read(artifact.representation.path) == operation_.source_artifact_id
    assert driver.cap_list("example") == (artifact,)


@pytest.mark.parametrize("alias", [None, "source", "destination", "both"])
def test_rsync_does_not_copy_nested_destination(tmp_path, alias):
    if shutil.which("rsync") is None:
        pytest.skip("rsync is not installed")

    source = tmp_path / "source"
    destination = source / "backups[1]*?"
    decoy = source / "backups1fooX"
    destination.mkdir(parents=True)
    decoy.mkdir()
    (source / "content").write_text("backup content")
    (destination / "old-backup").write_text("must not be copied")
    (decoy / "included").write_text("must be copied")

    source_path, destination_path = source, destination
    if alias in ("source", "both"):
        source_path = tmp_path / "source-alias"
        source_path.symlink_to(source, target_is_directory=True)
    if alias in ("destination", "both"):
        destination_path = tmp_path / "destination-alias"
        destination_path.symlink_to(destination, target_is_directory=True)

    result = Pipeline(DirectoryDriver(source_path), RsyncDriver(destination_path)).execute(
        operation()
    )

    artifact = result.representation.path
    assert (artifact / "content").read_text() == "backup content"
    assert (artifact / "backups1fooX" / "included").read_text() == "must be copied"
    assert not (artifact / destination.name).exists()


@pytest.mark.parametrize("fail", [False, True])
def test_rsync_only_lists_completed_transfers(tmp_path, monkeypatch, fail):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "content").write_text("payload")
    driver = RsyncDriver(destination)
    run = driver.runner.run

    def observe(command, **options):
        result = run(command, **options)
        if command[0] == "rsync":
            assert driver.cap_list("example") == ()
            assert not (destination / operation().artifact_name).exists()
            if fail:
                raise RuntimeError("transfer failed")
        return result

    monkeypatch.setattr(driver.runner, "run", observe)
    if fail:
        with pytest.raises(RuntimeError, match="transfer failed"):
            driver.cap_store(PathTree(source), operation())
        assert not tuple(destination.iterdir())
    else:
        artifact = driver.cap_store(PathTree(source), operation())
        assert driver.cap_list("example") == (artifact,)
        assert tuple(destination.iterdir()) == (artifact.representation.path,)


@pytest.mark.parametrize("skip_unchanged", [False, True])
def test_rsync_requires_working_metadata_only_for_skip_unchanged(
    tmp_path, monkeypatch, skip_unchanged
):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "content").write_text("payload")
    driver = RsyncDriver(destination)
    assert driver._source_id.write(source, "must-not-inherit")
    user_attr = XAttr("example", CommandRunner())
    assert user_attr.write(source, "directory attribute")
    assert user_attr.write(source / "content", "file attribute")
    monkeypatch.setattr(driver._source_id, "write", lambda path, value: False)
    operation_ = dataclasses.replace(replicated_operation(), skip_unchanged=skip_unchanged)

    if skip_unchanged:
        with pytest.raises(RsyncDriverError, match="skip_unchanged requires.*extended attributes"):
            driver.cap_store(RsyncTree(source), operation_)
        assert not tuple(destination.iterdir())
    else:
        artifact = driver.cap_store(RsyncTree(source), operation_)
        assert (artifact.representation.path / "content").read_text() == "payload"
        assert driver.cap_list("example")[0].operation.source_artifact_id is None
        assert tuple(destination.iterdir()) == (artifact.representation.path,)
        assert user_attr.read(artifact.representation.path) == "directory attribute"
        assert user_attr.read(artifact.representation.path / "content") == "file attribute"


@pytest.mark.parametrize("skip_unchanged", [False, True])
def test_rsync_xattr_check_is_conditional_and_uses_destination_endpoint(tmp_path, skip_unchanged):
    local = Backup("local", DirectoryDriver(tmp_path / "source"), RsyncDriver(tmp_path / "local"))
    target = SSHTarget("ssh://host", tmp_path / "key")
    replica = Backup(
        "offsite",
        BackupSource("local"),
        RsyncDriver(tmp_path / "offsite", target),
        skip_unchanged=skip_unchanged,
    )

    checks = CheckSubcommand._backup_checks(replica, {"local": local})

    metadata_checks = [check for check in checks if check.description.startswith("xattr tools")]
    assert len(metadata_checks) == int(skip_unchanged)
    assert all(check.ssh == target for check in metadata_checks)
