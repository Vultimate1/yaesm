"""Execution of external commands and command pipelines."""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import shlex
import subprocess
import tempfile

import yaesm.ty as ty
from yaesm.errors import YaesmError

Command: ty.TypeAlias = ty.Sequence[str | ty.Path]
logger = logging.getLogger(__name__)


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
        commands: ty.Sequence[Command],
        *,
        capture_output: bool = False,
        check: bool = True,
    ) -> CommandResult:
        """Run commands connected by pipes and check every exit status."""
        normalized = tuple(tuple(str(arg) for arg in command) for command in commands)
        if not normalized:
            raise ValueError("a command pipeline cannot be empty")
        if any(not command for command in normalized):
            raise ValueError("a command cannot be empty")

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

                output, _stderr = processes[-1].communicate()
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


def _terminate(processes: ty.Sequence[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.stdout is not None:
            process.stdout.close()
        if process.poll() is None:
            process.terminate()
    for process in processes:
        process.wait()
