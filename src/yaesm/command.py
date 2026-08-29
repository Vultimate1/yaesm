"""Execution of external commands and command pipelines."""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import shlex
import subprocess
import tempfile
import time

import yaesm.ty as ty
from yaesm.errors import YaesmError
from yaesm.logging import current_backup, format_duration

if ty.TYPE_CHECKING:
    from yaesm.ssh import SSHTarget

Command: ty.TypeAlias = ty.Sequence[str | ty.Path]
logger = logging.getLogger(__name__)
_STATUS_LOG_INTERVAL_SECONDS = 30


@dataclasses.dataclass(frozen=True, init=False)
class CommandStage:
    """A pipeline command and the SSH connection on which it runs."""

    command: tuple[str, ...]
    ssh: SSHTarget | None

    def __init__(self, command: Command, ssh: SSHTarget | None = None) -> None:
        object.__setattr__(self, "command", tuple(str(argument) for argument in command))
        object.__setattr__(self, "ssh", ssh)

    def execution_command(self) -> tuple[str, ...]:
        """Return the local command used to execute this stage."""
        return self.command if self.ssh is None else self.ssh.openssh_command(self.command)


PipelineCommand: ty.TypeAlias = Command | CommandStage


@dataclasses.dataclass(frozen=True)
class CommandResult:
    """Captured text output from one or more commands."""

    stdout: str | None
    stderr: str
    returncodes: tuple[int, ...]

    @property
    def returncode(self) -> int:
        """Return the rightmost nonzero status, or zero if all commands passed."""
        return next((status for status in reversed(self.returncodes) if status), 0)


class CommandError(YaesmError):
    """Raised when a command cannot start or exits unsuccessfully."""

    def __init__(self, command: Command, returncode: int | None, stderr: str) -> None:
        self.command = tuple(str(argument) for argument in command)
        self.returncode = returncode
        self.stderr = stderr
        if returncode is None:
            message = f"could not start command: {shlex.join(self.command)}"
        else:
            message = f"command exited with status {returncode}: {shlex.join(self.command)}"
        if details := stderr.strip():
            message += "\n" + "\n".join(f"  {line}" for line in details.splitlines())
        super().__init__(message)


class CommandRunner:
    """Run commands without a shell."""

    def run(
        self,
        command: Command,
        *,
        capture_output: bool = False,
        check: bool = True,
    ) -> CommandResult:
        """Run one command."""
        return self.pipeline((command,), capture_output=capture_output, check=check)

    def pipeline(
        self,
        commands: ty.Sequence[PipelineCommand],
        *,
        capture_output: bool = False,
        check: bool = True,
    ) -> CommandResult:
        """Run commands connected by pipes and check every exit status."""
        if not commands:
            raise ValueError("a command pipeline cannot be empty")
        if any(
            not (command.command if isinstance(command, CommandStage) else command)
            for command in commands
        ):
            raise ValueError("a command cannot be empty")
        normalized = _execution_commands(commands)

        logger.debug("exec: %s", " | ".join(shlex.join(command) for command in normalized))
        processes: list[subprocess.Popen[bytes]] = []
        with contextlib.ExitStack() as stack:
            stderr_files = [
                stack.enter_context(tempfile.TemporaryFile()) for _command in normalized
            ]
            previous_stdout = None
            try:
                for index, command in enumerate(normalized):
                    process = subprocess.Popen(
                        command,
                        stdin=previous_stdout,
                        stdout=(
                            subprocess.PIPE
                            if index < len(normalized) - 1 or capture_output
                            else None
                        ),
                        stderr=stderr_files[index],
                    )
                    if previous_stdout is not None:
                        previous_stdout.close()
                    previous_stdout = process.stdout
                    processes.append(process)

                started = time.monotonic()
                backup = current_backup.get()
                while True:
                    try:
                        output, _stderr = processes[-1].communicate(
                            timeout=_STATUS_LOG_INTERVAL_SECONDS if backup else None
                        )
                        break
                    except subprocess.TimeoutExpired:
                        logger.info(
                            "%s: command pipeline still running (%s elapsed)",
                            backup,
                            format_duration(time.monotonic() - started),
                        )
                for process in processes[:-1]:
                    process.wait()
            except OSError as error:
                _terminate(processes)
                raise CommandError(command, None, str(error)) from error
            except BaseException:
                _terminate(processes)
                raise

            stderrs = []
            for stderr_file in stderr_files:
                stderr_file.seek(0)
                stderrs.append(stderr_file.read().decode("utf-8", errors="replace"))

        returncodes = []
        for process in processes:
            assert process.returncode is not None
            returncodes.append(process.returncode)

        if check:
            for command, returncode, stderr in reversed(
                tuple(zip(normalized, returncodes, stderrs, strict=True))
            ):
                if returncode:
                    raise CommandError(command, returncode, stderr)

        return CommandResult(
            stdout=None if output is None else output.decode("utf-8", errors="replace"),
            stderr="\n".join(stderr.rstrip() for stderr in stderrs if stderr).rstrip(),
            returncodes=tuple(returncodes),
        )


def run(
    command: Command,
    *,
    capture_output: bool = False,
    check: bool = True,
) -> CommandResult:
    """Run one command."""
    return CommandRunner().run(command, capture_output=capture_output, check=check)


def _execution_commands(
    commands: ty.Sequence[PipelineCommand],
) -> tuple[tuple[str, ...], ...]:
    execution_commands = []
    index = 0
    while index < len(commands):
        stage = commands[index]
        if not isinstance(stage, CommandStage) or stage.ssh is None:
            execution_commands.append(
                stage.execution_command()
                if isinstance(stage, CommandStage)
                else tuple(str(argument) for argument in stage)
            )
            index += 1
            continue

        ssh = stage.ssh
        group = [stage.command]
        index += 1
        while index < len(commands):
            following = commands[index]
            if not isinstance(following, CommandStage) or following.ssh != ssh:
                break
            group.append(following.command)
            index += 1
        execution_commands.append(ssh.openssh_pipeline(group))
    return tuple(execution_commands)


def _terminate(processes: ty.Sequence[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.stdout is not None:
            process.stdout.close()
        if process.poll() is None:
            process.terminate()
    for process in processes:
        process.wait()
