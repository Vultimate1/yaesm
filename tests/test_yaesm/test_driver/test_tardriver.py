"""Tests for yaesm.driver.tardriver."""

import shutil
from pathlib import Path

import pytest
import voluptuous as vlp

import yaesm.command as command_module
import yaesm.ty as ty
from yaesm.check import CheckRole
from yaesm.command import CommandResult, CommandRunner, CommandStage
from yaesm.driver.directorydriver import DirectoryDriver
from yaesm.driver.tardriver import TarDriver, TarStream
from yaesm.errors import YaesmValueError
from yaesm.pipeline import Pipeline, PipelineError
from yaesm.representation import DataProperty, PathTree, UncompressedStream
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
        {"unknown": True},
    ],
)
def test_config_schema_rejects_invalid_configuration(config):
    with pytest.raises(vlp.Invalid):
        TarDriver.config_schema()(config)


def test_config_schema_output_constructs_driver():
    driver = TarDriver(**TarDriver.config_schema()({"one_file_system": False}))

    assert driver.one_file_system is False
    assert driver.ssh is None


def test_constructor_rejects_invalid_one_file_system():
    with pytest.raises(YaesmValueError, match="one_file_system must be a boolean"):
        TarDriver(one_file_system=ty.cast(bool, "yes"))


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


def test_capabilities_only_create_archive_streams():
    driver = TarDriver()

    assert driver.capabilities() == {"export"}
    assert driver.capability_metadata("export").adds == {DataProperty.ARCHIVED}
    assert driver.capability_metadata("export").base is None


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
def test_tar_options_work_with_gnu_tar_and_bsdtar(tmp_path, executable):
    if shutil.which(executable) is None:
        pytest.skip(f"{executable} is required")
    source = tmp_path / "source"
    excluded = source / "archives[1]*?"
    decoy = source / "archives1fooX"
    excluded.mkdir(parents=True)
    decoy.mkdir()
    (source / "content").write_text("backup content")
    (excluded / "excluded").write_text("must not be archived")
    (decoy / "included").write_text("must be archived")
    command = (
        TarDriver()
        .cap_export(PathTree(source, excluded_paths=(Path(excluded.name),)))
        .stages[0]
        .command
    )
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
