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
    """Raised when an SSH target specification is invalid."""


@dataclasses.dataclass(frozen=True)
class SSHTarget:
    """An SSH endpoint and its OpenSSH configuration."""

    spec: dataclasses.InitVar[str]
    key: ty.Path
    ssh_config: ty.Path | None = None
    host: str = dataclasses.field(init=False)
    user: str | None = dataclasses.field(init=False)
    port: int | None = dataclasses.field(init=False)

    def __post_init__(self, spec: str) -> None:
        user, host, port = _parse_spec(spec)

        object.__setattr__(self, "key", Path(self.key))
        object.__setattr__(
            self,
            "ssh_config",
            None if self.ssh_config is None else Path(self.ssh_config),
        )
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "user", user)
        object.__setattr__(self, "port", port)

    @staticmethod
    def is_spec(spec: object) -> bool:
        """Return whether an object is a valid SSH target specification."""
        try:
            _parse_spec(spec)
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
                vlp.Required("spec"): str,
                vlp.Required("key"): absolute_path,
                vlp.Optional("ssh_config"): absolute_path,
            }
        )
        try:
            return cls(**schema(value))
        except (vlp.Invalid, SSHTargetError, TypeError) as error:
            raise SSHTargetError(f"invalid SSH target: {error}") from error

    def same_endpoint(self, other: SSHTarget) -> bool:
        """Return whether two targets use the same user, host, and port."""
        return (self.user, self.host, self.port) == (other.user, other.host, other.port)

    def openssh_options(self) -> tuple[str, ...]:
        """Return the OpenSSH options used for this target."""
        options = []
        if self.ssh_config is not None:
            options.extend(("-F", str(self.ssh_config)))
        if self.port is not None:
            options.extend(("-p", str(self.port)))
        options.extend(
            (
                "-o",
                f"IdentityFile={self.key}",
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
        destination = self.host if self.user is None else f"{self.user}@{self.host}"
        remote_command = shlex.join(str(arg) for arg in command)
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
        return self._spec()

    def _spec(self) -> str:
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


def command_for_target(target: SSHTarget | None, command: Command) -> tuple[str, ...]:
    """Return a command ready to run locally or through SSH."""
    if target is not None:
        return target.openssh_command(command)
    return tuple(str(argument) for argument in command)


def _parse_spec(spec: object) -> tuple[str | None, str, int | None]:
    if not isinstance(spec, str):
        raise SSHTargetError(f"invalid SSH target spec: {spec!r}")

    try:
        parsed = urllib.parse.urlsplit(spec)
        port = parsed.port
    except ValueError as error:
        raise SSHTargetError(f"invalid SSH target spec: {spec!r}") from error

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
        raise SSHTargetError(f"invalid SSH target spec: {spec!r}")

    user = None if parsed.username is None else urllib.parse.unquote(parsed.username)
    host = urllib.parse.unquote(parsed.hostname)
    if (
        host.startswith("-")
        or (user is not None and user.startswith("-"))
        or any(character.isspace() for character in host)
        or (user is not None and any(character.isspace() for character in user))
    ):
        raise SSHTargetError(f"invalid SSH target spec: {spec!r}")
    return user, host, port
