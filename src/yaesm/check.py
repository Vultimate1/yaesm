"""Read-only backup feasibility checks."""

from __future__ import annotations

import dataclasses
import enum

import yaesm.ty as ty
from yaesm.command import Command, CommandError, CommandResult, CommandRunner


class CheckRole(enum.Enum):
    """How a driver participates in a backup.

    A source supplies backup data, a transform changes data in transit, and a
    destination stores and manages backup artifacts.
    """

    SOURCE = "source"
    TRANSFORM = "transform"
    DESTINATION = "destination"


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """The pass or failure result of one feasibility check."""

    description: str
    failure: str | None = None
    stdout: str | None = None
    stderr: str | None = None

    @property
    def passed(self) -> bool:
        """Return whether the check passed."""
        return self.failure is None


@dataclasses.dataclass(frozen=True)
class Check:
    """A named feasibility check that can be run later."""

    description: str
    function: ty.Callable[[], CheckResult]

    @classmethod
    def command(
        cls,
        description: str,
        command: Command,
        runner: CommandRunner,
        *,
        validate: ty.Callable[[CommandResult], str | None] | None = None,
    ) -> Check:
        """Return a deferred check for a harmless command."""
        command = tuple(str(argument) for argument in command)

        def run() -> CheckResult:
            try:
                result = runner.run(command, capture_output=True, check=False)
            except CommandError as error:
                return CheckResult(
                    description,
                    f"could not start {command[0]}",
                    stderr=error.stderr or None,
                )
            failure = (
                None
                if result.returncode == 0
                else f"{command[0]} exited with status {result.returncode}"
            )
            if failure is None and validate is not None:
                failure = validate(result)
            return CheckResult(
                description,
                failure,
                result.stdout or None,
                result.stderr or None,
            )

        return cls(description, run)

    def run(self) -> CheckResult:
        """Run the check and return its result."""
        return self.function()
