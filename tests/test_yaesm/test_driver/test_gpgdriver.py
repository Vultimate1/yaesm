"""Tests for yaesm.driver.gpgdriver."""

import pytest
import voluptuous as vlp

import yaesm.ty as ty
from yaesm.driver.gpgdriver import GPGDriver, GPGStream
from yaesm.errors import YaesmValueError
from yaesm.representation import CommandStream, DataProperty, EncryptedStream


def test_name():
    assert GPGDriver.name() == "gpg"


def test_config_schema_accepts_shorthand(tmp_path):
    public_key = tmp_path / "backup-key.asc"

    assert GPGDriver.config_schema()(public_key) == {"public_key": public_key}


def test_config_schema_accepts_mapping(tmp_path):
    public_key = tmp_path / "backup-key.asc"

    assert GPGDriver.config_schema()({"public_key": str(public_key)}) == {"public_key": public_key}


@pytest.mark.parametrize("value", [None, 1, [], {}])
def test_config_schema_rejects_invalid_public_key_type(value):
    with pytest.raises(vlp.Invalid, match="public_key must be a path"):
        GPGDriver.config_schema()({"public_key": value})


def test_config_schema_rejects_relative_public_key():
    with pytest.raises(vlp.Invalid, match="public_key must be an absolute path"):
        GPGDriver.config_schema()("backup-key.asc")


@pytest.mark.parametrize("config", [{}, {"public_key": "/key", "unknown": True}])
def test_config_schema_rejects_invalid_mapping(config):
    with pytest.raises(vlp.Invalid):
        GPGDriver.config_schema()(config)


def test_config_schema_output_constructs_driver(tmp_path):
    config = GPGDriver.config_schema()(tmp_path / "backup-key.asc")

    assert GPGDriver(**config).public_key == tmp_path / "backup-key.asc"


def test_constructor_rejects_invalid_public_key_type():
    with pytest.raises(YaesmValueError, match="public_key must be a path"):
        GPGDriver(ty.cast(ty.Path, None))


def test_constructor_rejects_relative_public_key():
    with pytest.raises(YaesmValueError, match="public_key must be an absolute path"):
        GPGDriver(ty.Path("backup-key.asc"))


def test_cap_encrypt_appends_noninteractive_gpg_filter(tmp_path):
    public_key = tmp_path / "backup-key.asc"
    source = CommandStream((("produce", "data"),))

    stream = GPGDriver(public_key).cap_encrypt(source)

    assert stream == GPGStream(
        (
            ("produce", "data"),
            (
                "gpg",
                "--batch",
                "--no-tty",
                "--no-keyring",
                "--compress-algo",
                "none",
                "--recipient-file",
                str(public_key),
                "--encrypt",
            ),
        )
    )


def test_capability_advertises_only_encryption(tmp_path):
    driver = GPGDriver(tmp_path / "backup-key.asc")

    assert driver.capabilities() == {"encrypt"}
    assert driver.capability_metadata("encrypt").adds == {DataProperty.ENCRYPTED}


def test_gpg_stream_is_encrypted_command_stream():
    assert issubclass(GPGStream, EncryptedStream)
