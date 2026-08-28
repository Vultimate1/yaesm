"""Read-only backup feasibility checks."""

import dataclasses
import enum

import yaesm.ty as ty


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

    def run(self) -> CheckResult:
        """Run the check and return its result."""
        return self.function()
