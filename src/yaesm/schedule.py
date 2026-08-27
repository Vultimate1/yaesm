"""Backup schedules."""

import dataclasses

from apscheduler.triggers.base import BaseTrigger


@dataclasses.dataclass(frozen=True)
class Schedule:
    """A named collection of scheduler triggers."""

    name: str
    triggers: tuple[BaseTrigger, ...]
