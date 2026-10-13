"""Tests for yaesm.driver.tardriver."""

import os
import shutil
from pathlib import Path

import pytest
import voluptuous as vlp

import yaesm.command as command_module
import yaesm.ty as ty
from yaesm.check import CheckRole
from yaesm.command import CommandError, CommandResult, CommandRunner, CommandStage
from yaesm.driver.directorydriver import DirectoryDriver
from yaesm.driver.driverbase import DriverError
from yaesm.driver.tardriver import TarDriver, TarStream, TarTree
from yaesm.errors import YaesmValueError
from yaesm.pipeline import Pipeline, PipelineError
from yaesm.representation import CommandStream, DataProperty, PathTree, UncompressedStream
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


def test_name():
    assert TarDriver.name() == "tar"


def test_config_schema_defaults_to_one_file_system():
    assert TarDriver.config_schema()({}) == {"one_file_system": True}
    assert TarDriver().tmpdir is None


def test_config_schema_accepts_one_file_system():
    assert TarDriver.config_schema()({"one_file_system": False}) == {
        "one_file_system": False,
    }


@pytest.mark.parametrize(
    "config",
    [
        None,
        [],
        "/archives",
        {"one_file_system": None},
        {"one_file_system": 1},
        {"one_file_system": "yes"},
        {"tmpdir": ""},
        {"tmpdir": "relative/path"},
        {"tmpdir": False},
        {"tmpdir": 42},
        {"tmpdir": []},
        {"unknown": True},
    ],
)
def test_config_schema_rejects_invalid_configuration(config):
    with pytest.raises(vlp.Invalid):
        TarDriver.config_schema()(config)


@pytest.mark.parametrize("tmpdir", [None, "/scratch", Path("/scratch")])
def test_config_schema_output_constructs_driver(tmpdir):
    driver = TarDriver(**TarDriver.config_schema()({"one_file_system": False, "tmpdir": tmpdir}))

    assert driver.one_file_system is False
    assert driver.ssh is None
    assert driver.tmpdir == (None if tmpdir is None else Path(tmpdir))


def test_constructor_rejects_invalid_one_file_system():
    with pytest.raises(YaesmValueError, match="one_file_system must be a boolean"):
        TarDriver(one_file_system=ty.cast(bool, "yes"))


@pytest.mark.parametrize("tmpdir", ["", "relative/path", False, 42, []])
def test_constructor_rejects_invalid_tmpdir(tmpdir):
    with pytest.raises(YaesmValueError, match="tmpdir must be"):
        TarDriver(tmpdir=tmpdir)


@pytest.mark.parametrize("suffixes", [(), (".zst",), (".tar", ".zst")])
def test_detransform_rejects_invalid_suffixes_before_running_commands(monkeypatch, suffixes):
    driver = TarDriver()

    def unexpected_command(*_args, **_options):
        pytest.fail("Invalid suffixes must be rejected before running commands")

    monkeypatch.setattr(driver.runner, "run", unexpected_command)
    monkeypatch.setattr(driver.runner, "pipeline", unexpected_command)

    with pytest.raises(DriverError, match=r"requires a \.tar suffix"):
        driver.cap_detransform(CommandStream(suffixes=suffixes))


@pytest.mark.parametrize("stdout", [None, "", "\n"])
def test_detransform_rejects_empty_mktemp_output(monkeypatch, stdout):
    driver = TarDriver()

    def run(_command, **_options):
        return CommandResult(stdout, "", (0,))

    def unexpected_operation(*_args, **_options):
        pytest.fail("Missing mktemp output must not trigger extraction or cleanup")

    monkeypatch.setattr(driver.runner, "run", run)
    monkeypatch.setattr(driver.runner, "pipeline", unexpected_operation)
    monkeypatch.setattr(driver, "cap_cleanup", unexpected_operation)

    with pytest.raises(DriverError, match="mktemp did not return a temporary directory"):
        driver.cap_detransform(CommandStream(suffixes=(".tar",)))


@pytest.mark.parametrize("configured", [False, True])
def test_detransform_uses_configured_ssh_target(tmp_path, monkeypatch, configured):
    target = SSHTarget("ssh://extraction", tmp_path / "key")
    source_target = SSHTarget("ssh://source", tmp_path / "key")
    extracted = Path("/scratch space/.yaesm-tar.ABC123")
    driver = TarDriver(ssh=target, tmpdir=extracted.parent if configured else None)
    source = CommandStream(
        (CommandStage(("cat", "/archives/source.tar"), source_target),),
        suffixes=(".tar",),
    )
    commands = []
    pipelines = []

    def run(command, **options):
        commands.append((tuple(command), options))
        return CommandResult(f"{extracted}\n", "", (0,))

    def pipeline(stages, **_options):
        pipelines.append(tuple(stages))
        return CommandResult(None, "", (0,))

    monkeypatch.setattr(driver.runner, "run", run)
    monkeypatch.setattr(driver.runner, "pipeline", pipeline)

    tree = driver.cap_detransform(source)

    template = (extracted.parent / ".yaesm-tar.XXXXXX",) if configured else ()
    assert commands == [
        (target.openssh_command(("mktemp", "-d", *template)), {"capture_output": True}),
        (target.openssh_command(("mkdir", "--", extracted / "data")), {}),
    ]
    assert len(pipelines) == 1
    assert pipelines[0][:-1] == source.stages
    extraction = pipelines[0][-1]
    assert extraction.ssh == target
    assert extraction.command[:4] == ("tar", "-x", "-f", "-")
    assert extraction.command[-2:] == ("-C", str(tree.path))
    assert tree == TarTree(extracted / "data", target, temporary_directory=extracted)


@pytest.mark.parametrize("configured", [False, True])
def test_detransform_creates_directory_in_selected_tmpdir(tmp_path, monkeypatch, configured):
    default_parent = tmp_path / "default"
    configured_parent = tmp_path / "configured scratch"
    source = tmp_path / "source"
    for directory in (default_parent, configured_parent, source):
        directory.mkdir()
    content = b"backup content\x00\xff\n"
    (source / "content").write_bytes(content)
    monkeypatch.setenv("TMPDIR", str(default_parent))
    driver = TarDriver(tmpdir=configured_parent if configured else None)

    tree = driver.cap_detransform(driver.cap_export(PathTree(source)))

    selected_parent = configured_parent if configured else default_parent
    unused_parent = default_parent if configured else configured_parent
    directories = tuple(selected_parent.iterdir())
    assert len(directories) == 1
    assert directories[0].is_dir()
    assert not tuple(unused_parent.iterdir())
    assert tree.temporary_directory == directories[0]
    assert tree.path.parent == directories[0]
    assert (tree.path / "content").read_bytes() == content
    driver.cap_cleanup(tree)


def test_detransform_keeps_public_archive_inside_private_directory(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    source.chmod(0o755)
    (source / "content").write_text("private backup content")
    (source / "content").chmod(0o644)
    driver = TarDriver(tmpdir=tmp_path)

    tree = driver.cap_detransform(driver.cap_export(PathTree(source)))

    try:
        assert tree.path.parent == tree.temporary_directory
        assert tree.temporary_directory.stat().st_mode & 0o777 == 0o700
        assert (tree.path / "content").read_text() == "private backup content"
    finally:
        driver.cap_cleanup(tree)

    assert not tree.temporary_directory.exists()


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_detransform_cleans_up_failure_and_preserves_error(tmp_path, monkeypatch, cleanup_fails):
    archive = tmp_path / "invalid.tar"
    archive.write_bytes(b"not a tar archive")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    driver = TarDriver(tmpdir=scratch)
    cleanup = driver.cap_cleanup
    cleanup_calls = []

    def attempt_cleanup(tree):
        cleanup_calls.append(tree)
        if cleanup_fails:
            raise RuntimeError("cleanup failed")
        cleanup(tree)

    monkeypatch.setattr(driver, "cap_cleanup", attempt_cleanup)
    source = CommandStream((CommandStage(("cat", archive)),), suffixes=(".tar",))

    with pytest.raises(CommandError) as error:
        driver.cap_detransform(source)

    assert error.value.command[0] == "tar"
    assert len(cleanup_calls) == 1
    assert cleanup_calls[0].path.exists() is cleanup_fails
    if not cleanup_fails:
        assert not tuple(scratch.iterdir())


def test_checks_only_require_tar(monkeypatch):
    calls = []

    def run(command, **_options):
        calls.append(tuple(command))
        return CommandResult(None, "", (0,))

    monkeypatch.setattr(command_module, "run", run)
    checks = TarDriver().check(CheckRole.TRANSFORM)

    assert tuple(check.description for check in checks) == ("tar is installed",)
    assert all(check.run().passed for check in checks)
    assert calls == [("tar", "--version")]


def test_checks_run_remotely(tmp_path, monkeypatch):
    target = SSHTarget("ssh://host", tmp_path / "key")
    calls = []

    def run(command, **_options):
        calls.append(tuple(command))
        return CommandResult(None, "", (0,))

    monkeypatch.setattr(command_module, "run", run)

    for check in TarDriver(ssh=target).check(CheckRole.TRANSFORM):
        check.run()

    assert calls == [target.openssh_command(("tar", "--version"))]


def test_capabilities_include_cleanup_detransform_and_export():
    driver = TarDriver()

    assert driver.capabilities() == {"cleanup", "detransform", "export"}
    assert driver.capability_metadata("cleanup").pipeline is False
    assert driver.capability_metadata("export").adds == {DataProperty.ARCHIVED}
    assert driver.capability_metadata("export").base is None


@pytest.mark.parametrize("mode", [0o555, 0o000])
def test_cap_cleanup_removes_readonly_tree_without_following_symlinks(tmp_path, mode):
    temporary_directory = tmp_path / ".yaesm-tar.test"
    tree = temporary_directory / "data"
    nested = tree / "nested"
    nested.mkdir(parents=True)
    (nested / "content").write_text("extracted content")
    original = tmp_path / "original"
    original.mkdir()
    (original / "content").write_text("original content")
    (tree / "link").symlink_to(original, target_is_directory=True)
    original.chmod(0o555)
    nested.chmod(mode)
    tree.chmod(mode)

    try:
        TarDriver().cap_cleanup(TarTree(tree, temporary_directory=temporary_directory))

        assert not temporary_directory.exists()
        assert (original / "content").read_text() == "original content"
        assert original.stat().st_mode & 0o777 == 0o555
    finally:
        original.chmod(0o700)
        if tree.exists():
            tree.chmod(0o700)
            nested.chmod(0o700)


def test_cap_cleanup_uses_representation_ssh_target(tmp_path, monkeypatch):
    driver_target = SSHTarget("ssh://driver", tmp_path / "key")
    tree_target = SSHTarget("ssh://extraction", tmp_path / "key")
    temporary_directory = Path("/scratch/.yaesm-tar.test")
    tree = TarTree(
        temporary_directory / "data", tree_target, temporary_directory=temporary_directory
    )
    driver = TarDriver(ssh=driver_target)
    calls = []

    def run(command, **_options):
        calls.append(tuple(command))
        return CommandResult(None, "", (0,))

    monkeypatch.setattr(driver.runner, "run", run)

    driver.cap_cleanup(tree)

    assert calls == [
        tree_target.openssh_command(
            (
                "find",
                "-P",
                temporary_directory,
                "-type",
                "d",
                "-exec",
                "chmod",
                "u+rwx",
                "--",
                "{}",
                ";",
            )
        ),
        tree_target.openssh_command(("rm", "-rf", "--", temporary_directory)),
    ]


def test_tar_stream_representation():
    assert issubclass(TarStream, UncompressedStream)
    assert TarStream.suffix == ".tar"


def test_cap_export_creates_complete_portable_archive():
    stream = TarDriver().cap_export(PathTree(Path("/source")))

    assert stream == TarStream((CommandStage(_TAR_COMMAND),), suffixes=(".tar",))


def test_cap_export_can_cross_filesystem_boundaries():
    stream = TarDriver(one_file_system=False).cap_export(PathTree(Path("/source")))

    assert stream.stages == (
        CommandStage(tuple(option for option in _TAR_COMMAND if option != "--one-file-system")),
    )


def test_cap_export_runs_tar_on_source_target(tmp_path):
    target = SSHTarget("ssh://backup-source", tmp_path / "key")
    source = PathTree(Path("/source"), target)

    stream = TarDriver().cap_export(source)

    assert stream.stages == (CommandStage(_TAR_COMMAND, target),)


def test_cap_export_excludes_protected_paths():
    stream = TarDriver().cap_export(
        PathTree(
            Path("/source"),
            excluded_paths=(Path("archives"),),
        )
    )

    assert stream.stages == (
        CommandStage(
            (
                *_TAR_COMMAND[:-3],
                "--exclude=./archives",
                *_TAR_COMMAND[-3:],
            )
        ),
    )


def test_tar_cannot_be_a_destination():
    with pytest.raises(PipelineError, match="tar provides no storage capability"):
        Pipeline(DirectoryDriver(Path("/source")), TarDriver())


@pytest.mark.parametrize("executable", ["tar", "bsdtar"])
def test_tar_roundtrip_with_gnu_tar_and_bsdtar(tmp_path, monkeypatch, executable):
    executable_path = shutil.which(executable)
    if executable_path is None:
        pytest.skip(f"{executable} is required")
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    (bin_directory / "tar").symlink_to(executable_path)
    monkeypatch.setenv("PATH", f"{bin_directory}{os.pathsep}{os.environ['PATH']}")

    source = tmp_path / "source"
    excluded = source / "archives[1]*?"
    decoy = source / "archives1fooX"
    excluded.mkdir(parents=True)
    decoy.mkdir()
    (source / "nested").mkdir()
    (source / "empty").mkdir()
    content = b"nested backup content\x00\xff\n"
    (source / "nested" / "content").write_bytes(content)
    (source / "link").symlink_to(Path("nested") / "content")
    (source / "content").write_text("backup content")
    (excluded / "excluded").write_text("must not be archived")
    (decoy / "included").write_text("must be archived")
    driver = TarDriver(tmpdir=tmp_path)
    stream = driver.cap_export(
        PathTree(source, excluded_paths=(Path(excluded.name),)),
    )
    archive = tmp_path / f"{executable}.tar"

    driver.runner.pipeline(
        (
            *stream.stages,
            ("dd", f"of={archive}", "bs=1048576"),
        )
    )

    listing = CommandRunner().run(
        ("tar", "-t", "-f", archive),
        capture_output=True,
    )
    assert set((listing.stdout or "").splitlines()) == {
        "./",
        "./content",
        "./archives1fooX/",
        "./archives1fooX/included",
        "./nested/",
        "./nested/content",
        "./empty/",
        "./link",
    }

    tree = driver.cap_detransform(
        CommandStream((CommandStage(("cat", archive)),), suffixes=stream.suffixes),
    )

    try:
        assert tree.temporary_directory.stat().st_mode & 0o777 == 0o700
        assert (tree.path / "content").read_text() == "backup content"
        assert (tree.path / "nested" / "content").read_bytes() == content
        assert (tree.path / "empty").is_dir()
        assert not tuple((tree.path / "empty").iterdir())
        assert (tree.path / "link").is_symlink()
        assert (tree.path / "link").readlink() == Path("nested") / "content"
        assert (tree.path / "link").read_bytes() == content
        assert not (tree.path / excluded.name).exists()
        assert (tree.path / decoy.name / "included").read_text() == "must be archived"
    finally:
        driver.cap_cleanup(tree)

    assert not tree.path.exists()
    assert not tree.temporary_directory.exists()
