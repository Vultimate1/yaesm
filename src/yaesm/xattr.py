"""Read and write user extended attributes locally or over SSH."""

import yaesm.ty as ty
from yaesm.check import Check, CheckResult
from yaesm.command import Command, CommandError, CommandRunner
from yaesm.ssh import SSHTarget, command_for_ssh


class XAttr:
    """A text attribute in the user namespace, accessed using native tools."""

    def __init__(self, name: str, runner: CommandRunner, ssh: SSHTarget | None = None) -> None:
        self.name = name
        self.runner = runner
        self.ssh = ssh
        self._cached_commands: tuple[Command, Command] | None = None

    def check(self) -> Check:
        description = "xattr tools are installed"
        if self.ssh is not None:
            description += f" on {self.ssh}"

        def run() -> CheckResult:
            failure = None
            try:
                if self._commands is None:
                    failure = "install getfattr/setfattr, xattr, or getextattr/setextattr"
            except CommandError as error:
                failure = str(error)
            return CheckResult(description, failure)

        return Check(description, run, ssh=self.ssh)

    def _run(self, command: Command) -> str | None:
        """Return command output, or None on failure (empty output is successful)."""
        result = self.runner.run(
            command_for_ssh(self.ssh, command), capture_output=True, check=False
        )
        return (result.stdout or "") if result.returncode == 0 else None

    @property
    def _commands(self) -> tuple[Command, Command] | None:
        if self._cached_commands is not None:
            return self._cached_commands
        for read, write in (
            (
                ("getfattr", "--only-values", "-n", f"user.{self.name}"),
                ("setfattr", "-n", f"user.{self.name}", "-v"),
            ),
            (("xattr", "-p", self.name), ("xattr", "-w", self.name)),
            (("getextattr", "-q", "user", self.name), ("setextattr", "user", self.name)),
        ):
            result = self._run(
                ("sh", "-c", 'command -v "$1" && command -v "$2"', "sh", read[0], write[0])
            )
            if result is not None:
                self._cached_commands = read, write
                return read, write
        return None

    def read(self, path: ty.Path) -> str | None:
        """Return the decoded value, or None if unavailable or malformed."""
        if self._commands is None:
            return None
        read, _write = self._commands
        result = self._run((*read, path))
        try:
            return bytes.fromhex(result).decode() if result is not None else None
        except ValueError:
            return None

    def write(self, path: ty.Path, value: str) -> bool:
        """Store hex text to avoid native escaping differences, and verify the value."""
        if self._commands is None:
            return False
        _read, write = self._commands
        return (
            self._run((*write, value.encode().hex(), path)) is not None and self.read(path) == value
        )
