"""SSH targets and OpenSSH command construction."""

from __future__ import annotations

import dataclasses
import shlex
import urllib.parse
from pathlib import Path

import voluptuous as vlp

import yaesm.ty as ty
from yaesm.command import Command, CommandResult, CommandRunner
from yaesm.errors import YaesmValueError


class SSHTargetError(YaesmValueError):
    """Raised when an SSH target is invalid."""


@dataclasses.dataclass(frozen=True)
class SSHTarget:
    """An SSH endpoint and its OpenSSH configuration."""

    endpoint: dataclasses.InitVar[str]
    identity_file: ty.Path
    config_file: ty.Path | None = None
    host: str = dataclasses.field(init=False)
    user: str | None = dataclasses.field(init=False)
    port: int | None = dataclasses.field(init=False)

    def __post_init__(self, endpoint: str) -> None:
        user, host, port = _parse_endpoint(endpoint)

        object.__setattr__(self, "identity_file", Path(self.identity_file))
        object.__setattr__(
            self,
            "config_file",
            None if self.config_file is None else Path(self.config_file),
        )
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "user", user)
        object.__setattr__(self, "port", port)

    @staticmethod
    def is_endpoint(endpoint: object) -> bool:
        """Return whether an object is a valid SSH endpoint."""
        try:
            _parse_endpoint(endpoint)
        except SSHTargetError:
            return False
        return True

    @classmethod
    def from_config(cls, value: object) -> SSHTarget:
        """Construct an SSH target from configuration data."""

        def absolute_path(value: object) -> Path:
            if not isinstance(value, str | Path):
                raise vlp.Invalid("must be a path")
            path = Path(value)
            if not path.is_absolute():
                raise vlp.Invalid("must be an absolute path")
            return path

        schema = vlp.Schema(
            {
                vlp.Required("endpoint"): str,
                vlp.Required("identity_file"): absolute_path,
                vlp.Optional("config_file"): absolute_path,
            }
        )
        try:
            return cls(**schema(value))
        except (vlp.Invalid, SSHTargetError, TypeError) as error:
            raise SSHTargetError(f"invalid SSH configuration: {error}") from error

    def same_endpoint(self, other: SSHTarget) -> bool:
        """Return whether two targets use the same user, host, and port."""
        return (self.user, self.host, self.port) == (other.user, other.host, other.port)

    def openssh_options(self) -> tuple[str, ...]:
        """Return the OpenSSH options used for this target."""
        options = []
        if self.config_file is not None:
            options.extend(("-F", str(self.config_file)))
        if self.port is not None:
            options.extend(("-p", str(self.port)))
        options.extend(
            (
                "-o",
                f"IdentityFile={self.identity_file}",
                "-o",
                "ControlMaster=auto",
                "-o",
                "ControlPath=~/.yaesm-ssh-controlmaster-%C",
                "-o",
                "ControlPersist=310",
                "-o",
                "RequestTTY=no",
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ConnectTimeout=60",
                "-o",
                "ServerAliveInterval=60",
                "-o",
                "ServerAliveCountMax=3",
                "-o",
                "PasswordAuthentication=no",
                "-o",
                "ClearAllForwardings=yes",
                "-o",
                "ForwardAgent=no",
                "-o",
                "ForwardX11=no",
            )
        )
        return tuple(options)

    def openssh_command(self, command: Command) -> tuple[str, ...]:
        """Return an OpenSSH command that safely quotes a remote command."""
        return self._openssh_command(shlex.join(str(arg) for arg in command))

    def openssh_pipeline(self, commands: ty.Sequence[Command]) -> tuple[str, ...]:
        """Return an OpenSSH command that safely runs a remote command pipeline."""
        if not commands:
            raise ValueError("an SSH pipeline cannot be empty")
        if any(not command for command in commands):
            raise ValueError("an SSH pipeline command cannot be empty")
        if len(commands) == 1:
            return self.openssh_command(commands[0])
        return self._openssh_command(_pipeline_script(commands))

    def _openssh_command(self, remote_command: str) -> tuple[str, ...]:
        destination = self.host if self.user is None else f"{self.user}@{self.host}"
        return ("ssh", *self.openssh_options(), destination, remote_command)

    def format_location(self, location: str | ty.Path) -> str:
        """Format a location on this endpoint as an SSH URI."""
        path = urllib.parse.quote(str(location), safe="/:@-._~")
        return f"{self}{'' if path.startswith('/') else '/'}{path}"

    def run(
        self,
        command: Command,
        *,
        runner: CommandRunner,
        capture_output: bool = False,
        check: bool = True,
    ) -> CommandResult:
        """Run a command on this target using ``runner``."""
        return runner.run(
            self.openssh_command(command),
            capture_output=capture_output,
            check=check,
        )

    def __str__(self) -> str:
        return self._endpoint()

    def _endpoint(self) -> str:
        host = urllib.parse.quote(self.host, safe=":-._~")
        if ":" in self.host:
            host = f"[{host}]"
        user = "" if self.user is None else f"{urllib.parse.quote(self.user, safe='-._~')}@"
        port = "" if self.port is None else f":{self.port}"
        return f"ssh://{user}{host}{port}"


def same_endpoint(first: SSHTarget | None, second: SSHTarget | None) -> bool:
    """Return whether two targets refer to the same local or SSH endpoint."""
    if first is None or second is None:
        return first is second
    return first.same_endpoint(second)


def command_for_ssh(ssh: SSHTarget | None, command: Command) -> tuple[str, ...]:
    """Return a command ready to run locally or through SSH."""
    if ssh is not None:
        return ssh.openssh_command(command)
    return tuple(str(argument) for argument in command)


def _parse_endpoint(endpoint: object) -> tuple[str | None, str, int | None]:
    if not isinstance(endpoint, str):
        raise SSHTargetError(f"invalid SSH endpoint: {endpoint!r}")

    try:
        parsed = urllib.parse.urlsplit(endpoint)
        port = parsed.port
    except ValueError as error:
        raise SSHTargetError(f"invalid SSH endpoint: {endpoint!r}") from error

    authority = parsed.netloc.rsplit("@", 1)[-1]
    if (
        parsed.scheme != "ssh"
        or parsed.hostname is None
        or parsed.password is not None
        or authority.endswith(":")
        or parsed.path
        or parsed.query
        or parsed.fragment
        or port == 0
    ):
        raise SSHTargetError(f"invalid SSH endpoint: {endpoint!r}")

    user = None if parsed.username is None else urllib.parse.unquote(parsed.username)
    host = urllib.parse.unquote(parsed.hostname)
    if (
        host.startswith("-")
        or (user is not None and user.startswith("-"))
        or any(character.isspace() for character in host)
        or (user is not None and any(character.isspace() for character in user))
    ):
        raise SSHTargetError(f"invalid SSH endpoint: {endpoint!r}")
    return user, host, port


def _pipeline_script(commands: ty.Sequence[Command]) -> str:
    last = len(commands) - 1
    pipes = tuple(f'"$_yaesm_pipeline/{index}"' for index in range(last))
    stages = []
    for index, command in enumerate(commands):
        rendered = shlex.join(str(argument) for argument in command)
        input_ = " <&9" if index == 0 else f" <{pipes[index - 1]}"
        if index == last:
            stages.append(f"{rendered}{input_}; _yaesm_result=$?; ")
        else:
            stages.append(f'{rendered}{input_} >{pipes[index]} & _yaesm_pids="$! $_yaesm_pids"; ')
    return (
        'umask 077; exec 9<&0; _yaesm_pipeline="/tmp/.yaesm-pipeline.$$"; _yaesm_pids=; '
        'mkdir "$_yaesm_pipeline" || exit 125; '
        "trap 'rm -rf \"$_yaesm_pipeline\"' 0; "
        "trap 'for _yaesm_pid in $_yaesm_pids; do "
        'kill "$_yaesm_pid" 2>/dev/null; done; exit 125\' 1 2 3 15; '
        f"mkfifo {' '.join(pipes)} || exit 125; "
        + "".join(stages)
        + 'for _yaesm_pid in $_yaesm_pids; do wait "$_yaesm_pid"; _yaesm_code=$?; '
        'if [ "$_yaesm_result" -eq 0 ] && [ "$_yaesm_code" -ne 0 ]; then '
        '_yaesm_result=$_yaesm_code; fi; done; exit "$_yaesm_result"'
    )
