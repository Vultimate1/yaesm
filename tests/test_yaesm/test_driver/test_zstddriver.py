"""Tests for yaesm.driver.zstddriver."""

import shutil

import pytest
import voluptuous as vlp

import yaesm.command as command_module
import yaesm.ty as ty
from yaesm.check import CheckRole
from yaesm.command import CommandResult, CommandRunner, CommandStage
from yaesm.driver.zstddriver import ZstdDriver, ZstdStream
from yaesm.errors import YaesmValueError
from yaesm.representation import CommandStream, CompressedStream, DataProperty
from yaesm.ssh import SSHTarget


def test_name():
    assert ZstdDriver.name() == "zstd"


def test_config_schema_uses_default_level():
    assert ZstdDriver.config_schema()({}) == {"level": 3}


@pytest.mark.parametrize("level", [1, 3, 19])
def test_config_schema_accepts_standard_levels(level):
    assert ZstdDriver.config_schema()({"level": level}) == {"level": level}


def test_config_schema_accepts_shorthand():
    assert ZstdDriver.config_schema()(7) == {"level": 7}


@pytest.mark.parametrize("level", [None, True, 0, 20, 3.0, "3"])
def test_config_schema_rejects_invalid_level(level):
    with pytest.raises(vlp.Invalid, match="level must be an integer from 1 to 19"):
        ZstdDriver.config_schema()({"level": level})


@pytest.mark.parametrize("config", [None, [], "3", {"unknown": True}])
def test_config_schema_rejects_invalid_structure(config):
    with pytest.raises(vlp.Invalid):
        ZstdDriver.config_schema()(config)


def test_config_schema_output_constructs_driver():
    config = ZstdDriver.config_schema()({"level": 7})

    assert ZstdDriver(**config).level == 7
    assert ZstdDriver(**config).ssh is None


@pytest.mark.parametrize("level", [True, 0, 20, 3.0])
def test_constructor_rejects_invalid_level(level):
    with pytest.raises(YaesmValueError, match="level must be an integer from 1 to 19"):
        ZstdDriver(ty.cast(int, level))


@pytest.mark.parametrize("role", tuple(CheckRole))
def test_checks_only_require_zstd_executable(role, monkeypatch):
    calls = []

    def run(command, *, capture_output=False, check=True):
        calls.append((command, capture_output, check))
        return CommandResult("zstd 1.5\n", "", (0,))

    monkeypatch.setattr(command_module, "run", run)
    driver = ZstdDriver()
    checks = driver.check(role)

    assert driver._checks(role) == ()
    assert tuple(check.description for check in checks) == ("zstd is installed",)
    assert checks[0].run().passed is True
    assert calls == [(("zstd", "--version"), True, False)]


def test_checks_run_on_configured_ssh(tmp_path, monkeypatch):
    ssh = SSHTarget("ssh://host", tmp_path / "identity")
    calls = []

    def run(command, *, capture_output=False, check=True):
        calls.append((command, capture_output, check))
        return CommandResult("zstd 1.5\n", "", (0,))

    monkeypatch.setattr(command_module, "run", run)

    result = ZstdDriver(ssh=ssh).check(CheckRole.TRANSFORM)[0].run()

    assert result.description == f"zstd is installed on {ssh}"
    assert result.passed is True
    assert calls == [(ssh.openssh_command(("zstd", "--version")), True, False)]


@pytest.mark.parametrize("suffixes", [(), (".tar",)])
def test_cap_compress_appends_zstd_filter(suffixes):
    source = CommandStream((CommandStage(("produce", "data")),), suffixes=suffixes)

    stream = ZstdDriver(level=7).cap_compress(source)

    assert stream == ZstdStream(
        (
            CommandStage(("produce", "data")),
            CommandStage(("zstd", "--compress", "--stdout", "--quiet", "-7")),
        ),
        suffixes=(*suffixes, ".zst"),
    )


def test_cap_compress_runs_on_configured_ssh(tmp_path):
    ssh = SSHTarget("ssh://host", tmp_path / "identity")
    source = CommandStream((CommandStage(("produce", "data"), ssh),))

    stream = ZstdDriver(level=7, ssh=ssh).cap_compress(source)

    assert stream.stages[-1] == CommandStage(
        ("zstd", "--compress", "--stdout", "--quiet", "-7"),
        ssh,
    )


def test_capability_advertises_only_compression():
    driver = ZstdDriver()

    assert driver.capabilities() == {"compress"}
    assert driver.capability_metadata("compress").adds == {DataProperty.COMPRESSED}


def test_zstd_stream_is_compressed_command_stream():
    assert issubclass(ZstdStream, CompressedStream)
    assert ZstdStream.suffix == ".zst"


def test_zstd_compresses_and_restores_stream():
    if shutil.which("zstd") is None:
        pytest.skip("Zstandard is not installed")
    source = CommandStream((CommandStage(("printf", "%s", "compressed backup content")),))
    stream = ZstdDriver().cap_compress(source)

    result = CommandRunner().pipeline(
        (
            *stream.stages,
            ("zstd", "--decompress", "--stdout", "--quiet"),
        ),
        capture_output=True,
    )

    assert result.stdout == "compressed backup content"
