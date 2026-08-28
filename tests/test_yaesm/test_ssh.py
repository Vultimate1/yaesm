"""Tests for yaesm.ssh."""

import shlex
from pathlib import Path

import pytest

from yaesm.command import Command, CommandResult, CommandRunner
from yaesm.ssh import SSHTarget, SSHTargetError, command_for_target, same_endpoint


class RecordingRunner(CommandRunner):
    def __init__(self) -> None:
        self.call: tuple[tuple[str, ...], bool, bool] | None = None

    def run(
        self,
        command: Command,
        *,
        capture_output: bool = False,
        check: bool = True,
    ) -> CommandResult:
        self.call = (tuple(str(arg) for arg in command), capture_output, check)
        return CommandResult("output", "error", (4,))


@pytest.mark.parametrize(
    ("spec", "user", "host", "port"),
    [
        ("ssh://host", None, "host", None),
        ("ssh://user@host", "user", "host", None),
        ("ssh://host:2222", None, "host", 2222),
        ("ssh://user@host:2222", "user", "host", 2222),
        ("ssh://user@[2001:db8::1]:2222", "user", "2001:db8::1", 2222),
    ],
)
def test_ssh_target_parses_spec(spec, user, host, port):
    target = SSHTarget(spec, Path("/key"))

    assert target.user == user
    assert target.host == host
    assert target.port == port
    assert str(target) == spec
    assert SSHTarget.is_spec(spec)


def test_ssh_target_parses_configuration():
    assert SSHTarget.from_config(
        {
            "spec": "ssh://user@host:2222",
            "key": "/key",
            "ssh_config": "/config",
        }
    ) == SSHTarget("ssh://user@host:2222", Path("/key"), Path("/config"))


@pytest.mark.parametrize(
    "spec",
    [
        "host:/backup",
        "ssh://host:/backup",
        "ssh://host:relative",
        "ssh://host/backup",
        "ssh://p2222:user@host:/backup",
        "ssh://host:0",
        "ssh://host:65536",
        "ssh://-oProxyCommand=bad",
        "ssh://-user@host",
        "ssh://user:password@host",
        "ssh://host?query",
        "ssh://host#fragment",
    ],
)
def test_ssh_target_rejects_invalid_spec(spec):
    assert not SSHTarget.is_spec(spec)
    with pytest.raises(SSHTargetError, match="invalid SSH target spec"):
        SSHTarget(spec, Path("/key"))


def test_ssh_target_compares_endpoints():
    target = SSHTarget("ssh://user@host:22", Path("/key"))

    assert target.same_endpoint(SSHTarget("ssh://user@host:22", Path("/other")))
    assert not target.same_endpoint(SSHTarget("ssh://user@host:23", Path("/key")))
    assert not target.same_endpoint(SSHTarget("ssh://other@host:22", Path("/key")))
    assert not target.same_endpoint(SSHTarget("ssh://user@other:22", Path("/key")))


def test_same_local_or_ssh_endpoint():
    target = SSHTarget("ssh://host", Path("/key"))

    assert same_endpoint(None, None)
    assert same_endpoint(target, SSHTarget("ssh://host", Path("/other-key")))
    assert not same_endpoint(None, target)
    assert not same_endpoint(target, None)
    assert not same_endpoint(target, SSHTarget("ssh://other", Path("/key")))


def test_command_for_local_target():
    assert command_for_target(None, ("command", Path("/a path"))) == (
        "command",
        "/a path",
    )


def test_command_for_ssh_target():
    target = SSHTarget("ssh://host", Path("/key"))

    assert command_for_target(target, ("command", Path("/a path"))) == (
        target.openssh_command(("command", Path("/a path")))
    )


def test_ssh_target_builds_options():
    target = SSHTarget("ssh://user@host:2222", Path("/key"), Path("/config"))

    options = target.openssh_options()

    assert options[:4] == ("-F", "/config", "-p", "2222")
    assert "IdentityFile=/key" in options
    assert "ControlPath=~/.yaesm-ssh-controlmaster-%C" in options
    assert "ConnectTimeout=60" in options
    assert "ServerAliveInterval=60" in options
    assert "ServerAliveCountMax=3" in options
    assert "ClearAllForwardings=yes" in options
    assert "ForwardAgent=no" in options
    assert "ForwardX11=no" in options


def test_ssh_target_quotes_remote_command():
    target = SSHTarget("ssh://user@host", Path("/key"))
    remote = ["printf", "%s\\n", "O'Brien", Path("/a path")]

    command = target.openssh_command(remote)

    assert command[0] == "ssh"
    assert command[-2] == "user@host"
    assert shlex.split(command[-1]) == [str(arg) for arg in remote]


def test_ssh_target_runs_through_command_runner():
    target = SSHTarget("ssh://user@host", Path("/key"))
    runner = RecordingRunner()
    path = Path("/backup")

    result = target.run(["test", "-d", path], runner=runner, check=False)

    assert runner.call == (target.openssh_command(["test", "-d", path]), False, False)
    assert result == CommandResult("output", "error", (4,))
