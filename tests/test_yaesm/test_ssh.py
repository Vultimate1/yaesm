"""Tests for yaesm.ssh."""

import shlex
from pathlib import Path

import pytest

from yaesm.command import Command, CommandResult, CommandRunner
from yaesm.ssh import SSHTarget, SSHTargetError, command_for_ssh, same_endpoint


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
    ("endpoint", "user", "host", "port"),
    [
        ("ssh://host", None, "host", None),
        ("ssh://user@host", "user", "host", None),
        ("ssh://host:2222", None, "host", 2222),
        ("ssh://user@host:2222", "user", "host", 2222),
        ("ssh://user@[2001:db8::1]:2222", "user", "2001:db8::1", 2222),
    ],
)
def test_ssh_target_parses_endpoint(endpoint, user, host, port):
    target = SSHTarget(endpoint, Path("/key"))

    assert target.user == user
    assert target.host == host
    assert target.port == port
    assert str(target) == endpoint
    assert SSHTarget.is_endpoint(endpoint)


def test_ssh_target_parses_configuration():
    target = SSHTarget.from_config(
        {
            "endpoint": "ssh://user@host:2222",
            "identity_file": "/key",
            "config_file": "/config",
        }
    )

    assert target == SSHTarget("ssh://user@host:2222", Path("/key"), Path("/config"))
    assert target.identity_file == Path("/key")
    assert target.config_file == Path("/config")


@pytest.mark.parametrize(
    "config",
    [
        {"spec": "ssh://host", "key": "/key"},
        {"endpoint": "ssh://host", "identity_file": "/key", "ssh_config": "/config"},
    ],
)
def test_ssh_target_rejects_old_configuration_vocabulary(config):
    with pytest.raises(SSHTargetError, match="invalid SSH configuration"):
        SSHTarget.from_config(config)


@pytest.mark.parametrize(
    "endpoint",
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
def test_ssh_target_rejects_invalid_endpoint(endpoint):
    assert not SSHTarget.is_endpoint(endpoint)
    with pytest.raises(SSHTargetError, match="invalid SSH endpoint"):
        SSHTarget(endpoint, Path("/key"))


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
    assert command_for_ssh(None, ("command", Path("/a path"))) == (
        "command",
        "/a path",
    )


def test_command_for_ssh_target():
    target = SSHTarget("ssh://host", Path("/key"))

    assert command_for_ssh(target, ("command", Path("/a path"))) == (
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


def test_ssh_target_formats_remote_location():
    target = SSHTarget("ssh://user@host:2222", Path("/key"))

    assert target.format_location("/backups/a path@snapshot") == (
        "ssh://user@host:2222/backups/a%20path@snapshot"
    )
    assert target.format_location("tank/backups@snapshot") == (
        "ssh://user@host:2222/tank/backups@snapshot"
    )


def test_ssh_target_runs_through_command_runner():
    target = SSHTarget("ssh://user@host", Path("/key"))
    runner = RecordingRunner()
    path = Path("/backup")

    result = target.run(["test", "-d", path], runner=runner, check=False)

    assert runner.call == (target.openssh_command(["test", "-d", path]), False, False)
    assert result == CommandResult("output", "error", (4,))
