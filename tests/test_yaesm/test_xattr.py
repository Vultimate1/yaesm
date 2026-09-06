"""Tests for yaesm.xattr."""

import logging

import pytest

from yaesm.command import CommandError, CommandResult, CommandRunner
from yaesm.ssh import SSHTarget
from yaesm.xattr import XAttr


class ShellSSHRunner(CommandRunner):
    def run(self, command, **options):
        if command[0] == "ssh":
            command = ("sh", "-c", command[-1])
        return super().run(command, **options)


@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("value", ["", "source 0x '$(false) %s\n\n\x00é"])
def test_round_trip(tmp_path, directory, remote, value):
    path = tmp_path / "artifact '$(false)"
    if directory:
        path.mkdir()
    else:
        path.write_bytes(b"\x00payload\xff")
    target = SSHTarget("ssh://host", tmp_path / "key") if remote else None
    attr = XAttr("yaesm.test", ShellSSHRunner(), target)

    assert attr.read(path) is None
    assert attr.write(path, value)
    assert XAttr("yaesm.test", ShellSSHRunner(), target).read(path) == value
    if directory:
        assert not tuple(path.iterdir())
    else:
        assert path.read_bytes() == b"\x00payload\xff"


@pytest.mark.parametrize("remote", [False, True])
def test_debug_logs_short_commands(tmp_path, caplog, remote):
    target = SSHTarget("ssh://host", tmp_path / "key") if remote else None
    attr = XAttr("yaesm.test", ShellSSHRunner(), target)
    path = tmp_path / "artifact"
    path.touch()

    with caplog.at_level(logging.DEBUG, logger="yaesm.command"):
        assert attr.write(path, "source-id")
        lookup_finished = len(caplog.messages)
        assert attr.read(path) == "source-id"
        assert attr.read(path) == "source-id"

    commands = caplog.messages
    assert any("command -v" in message for message in commands[:lookup_finished])
    assert all("command -v" not in message for message in commands[lookup_finished:])
    assert all("\n" not in message for message in commands)
    assert all(len(message) < 500 for message in commands)
    assert any(str(path) in message for message in commands)


@pytest.mark.parametrize(
    ("stdout", "returncode"), [("", 1), ("wrong value", 0), ("736f757263652d6964", 1)]
)
def test_rejects_failed_or_incorrect_write(tmp_path, monkeypatch, stdout, returncode):
    attr = XAttr("yaesm.test", CommandRunner())
    assert attr.check().run().passed
    monkeypatch.setattr(
        attr.runner, "run", lambda *args, **kwargs: CommandResult(stdout, "", (returncode,))
    )

    assert not attr.write(tmp_path / "artifact", "source-id")


@pytest.mark.parametrize("tools_available", [False, True])
def test_check_is_read_only(tmp_path, monkeypatch, tools_available):
    if not tools_available:
        tools = tmp_path / "tools"
        tools.mkdir()
        (tools / "sh").symlink_to("/bin/sh")
        monkeypatch.setenv("PATH", str(tools))
    attr = XAttr("yaesm.test", CommandRunner())

    assert attr.check().run().passed is tools_available
    assert sorted(path.name for path in tmp_path.iterdir()) == (
        [] if tools_available else ["tools"]
    )
    if not tools_available:
        assert attr.read(tmp_path / "missing") is None
        assert not attr.write(tmp_path / "missing", "value")


@pytest.mark.parametrize("returncode", [1, 255])
def test_lookup_recovers_after_failure(tmp_path, monkeypatch, returncode):
    target = SSHTarget("ssh://host", tmp_path / "key")
    attr = XAttr("yaesm.test", CommandRunner(), target)
    calls = []

    def run(command, **options):
        calls.append(command)
        return CommandResult("", "", (returncode,))

    monkeypatch.setattr(attr.runner, "run", run)
    check = attr.check()
    assert not check.run().passed

    returncode = 0
    assert check.run().passed
    calls_after_success = len(calls)
    assert check.run().passed
    assert len(calls) == calls_after_success
    assert all(command[0] == "ssh" for command in calls)


@pytest.mark.parametrize("stdout", ["not hex", "ff", "736f75726365"])
def test_read_rejects_invalid_or_failed_output(tmp_path, monkeypatch, stdout):
    attr = XAttr("yaesm.test", CommandRunner())
    assert attr.check().run().passed
    monkeypatch.setattr(
        attr.runner,
        "run",
        lambda *args, **kwargs: CommandResult(stdout, "", (int(stdout == "736f75726365"),)),
    )
    assert attr.read(tmp_path / "artifact") is None


def test_check_handles_command_start_failure_and_retries(tmp_path, monkeypatch):
    attr = XAttr("yaesm.test", CommandRunner())
    run = attr.runner.run

    def fail(*args, **kwargs):
        raise CommandError(("sh",), 127, "could not start")

    monkeypatch.setattr(attr.runner, "run", fail)
    assert not attr.check().run().passed
    monkeypatch.setattr(attr.runner, "run", run)
    assert attr.check().run().passed
