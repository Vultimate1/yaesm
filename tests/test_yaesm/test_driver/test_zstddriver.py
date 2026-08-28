"""Tests for yaesm.driver.zstddriver."""

import pytest
import voluptuous as vlp

import yaesm.command as command_module
import yaesm.ty as ty
from yaesm.check import CheckRole
from yaesm.command import CommandResult
from yaesm.driver.zstddriver import ZstdDriver, ZstdStream
from yaesm.errors import YaesmValueError
from yaesm.representation import CommandStream, CompressedStream, DataProperty


def test_name():
    assert ZstdDriver.name() == "zstd"


def test_config_schema_uses_default_level():
    assert ZstdDriver.config_schema()({}) == {"level": 3}


@pytest.mark.parametrize("level", [1, 3, 19])
def test_config_schema_accepts_standard_levels(level):
    assert ZstdDriver.config_schema()({"level": level}) == {"level": level}


@pytest.mark.parametrize("level", [None, True, 0, 20, 3.0, "3"])
def test_config_schema_rejects_invalid_level(level):
    with pytest.raises(vlp.Invalid, match="level must be an integer from 1 to 19"):
        ZstdDriver.config_schema()({"level": level})


@pytest.mark.parametrize("config", [None, [], 1, {"unknown": True}])
def test_config_schema_rejects_invalid_structure(config):
    with pytest.raises(vlp.Invalid):
        ZstdDriver.config_schema()(config)


def test_config_schema_output_constructs_driver():
    config = ZstdDriver.config_schema()({"level": 7})

    assert ZstdDriver(**config).level == 7


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


def test_cap_compress_appends_zstd_filter():
    source = CommandStream((("produce", "data"),))

    stream = ZstdDriver(level=7).cap_compress(source)

    assert stream == ZstdStream(
        (
            ("produce", "data"),
            ("zstd", "--compress", "--stdout", "--quiet", "-7"),
        )
    )


def test_capability_advertises_only_compression():
    driver = ZstdDriver()

    assert driver.capabilities() == {"compress"}
    assert driver.capability_metadata("compress").adds == {DataProperty.COMPRESSED}


def test_zstd_stream_is_compressed_command_stream():
    assert issubclass(ZstdStream, CompressedStream)
