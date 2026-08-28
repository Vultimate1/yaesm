"""Tests for yaesm.driver.gpgdriver."""

import os

import pytest
import voluptuous as vlp

import yaesm.command as command_module
import yaesm.ty as ty
from yaesm.check import CheckResult, CheckRole
from yaesm.command import Command, CommandError, CommandResult, CommandRunner
from yaesm.driver.gpgdriver import GPGDriver, GPGStream
from yaesm.errors import YaesmValueError
from yaesm.representation import CommandStream, DataProperty, EncryptedStream


class RecordingRunner(CommandRunner):
    def __init__(self, *results: CommandResult | CommandError) -> None:
        self.results = list(results)
        self.calls: list[tuple[tuple[str, ...], bool, bool]] = []

    def run(
        self,
        command: Command,
        *,
        capture_output: bool = False,
        check: bool = True,
    ) -> CommandResult:
        normalized = tuple(str(argument) for argument in command)
        self.calls.append((normalized, capture_output, check))
        result = self.results.pop(0)
        if isinstance(result, CommandError):
            raise result
        return result


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


def test_checks_are_deferred_and_capture_output(tmp_path, monkeypatch):
    public_key = tmp_path / "backup-key.asc"
    runner = RecordingRunner(
        CommandResult("gpg 2.4\n", "", (0,)),
        CommandResult("", "gpg warning\n", (0,)),
    )
    monkeypatch.setattr(command_module, "run", runner.run)
    driver = GPGDriver(public_key)
    checks = driver.check(CheckRole.TRANSFORM)

    assert tuple(check.description for check in checks) == (
        "gpg is installed",
        f"public key can encrypt data: {public_key}",
    )
    assert runner.calls == []

    executable_result, key_result = (check.run() for check in checks)

    assert executable_result.passed is True
    assert executable_result.stdout == "gpg 2.4\n"
    assert key_result.passed is True
    assert key_result.stdout is None
    assert key_result.stderr == "gpg warning\n"
    assert runner.calls == [
        (("gpg", "--version"), True, False),
        (
            (
                "gpg",
                "--batch",
                "--no-tty",
                "--no-keyring",
                "--compress-algo",
                "none",
                "--recipient-file",
                str(public_key),
                "--output",
                os.devnull,
                "--encrypt",
                str(public_key),
            ),
            True,
            False,
        ),
    ]


def test_key_check_reports_command_failure(tmp_path, monkeypatch):
    public_key = tmp_path / "bad-key.asc"
    runner = RecordingRunner(CommandResult(None, "invalid public key", (2,)))
    monkeypatch.setattr(command_module, "run", runner.run)
    driver = GPGDriver(public_key)

    result = driver.check(CheckRole.TRANSFORM)[1].run()

    assert result.passed is False
    assert result.failure == "gpg exited with status 2"
    assert result.stderr == "invalid public key"


def test_key_check_reports_start_failure(tmp_path, monkeypatch):
    public_key = tmp_path / "bad-key.asc"
    error = CommandError(("gpg", "--encrypt"), None, "Permission denied")
    runner = RecordingRunner(error)
    monkeypatch.setattr(command_module, "run", runner.run)

    result = GPGDriver(public_key)._checks(CheckRole.TRANSFORM)[0].run()

    assert result == CheckResult(
        f"public key can encrypt data: {public_key}",
        "could not start gpg",
        stderr="Permission denied",
    )


def test_executable_check_reports_start_failure(tmp_path, monkeypatch):
    error = CommandError(("gpg", "--version"), None, "No such file or directory")
    runner = RecordingRunner(error)
    monkeypatch.setattr(command_module, "run", runner.run)
    driver = GPGDriver(tmp_path / "key")

    result = driver.check(CheckRole.TRANSFORM)[0].run()

    assert result.passed is False
    assert result.failure == "could not start gpg"
    assert result.stderr == "No such file or directory"


@pytest.mark.parametrize("role", [CheckRole.SOURCE, CheckRole.DESTINATION])
def test_key_check_is_only_used_for_transform_role(tmp_path, role):
    driver = GPGDriver(tmp_path / "key")
    checks = driver.check(role)

    assert tuple(check.description for check in checks) == ("gpg is installed",)
    assert driver._checks(role) == ()


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
