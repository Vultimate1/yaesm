"""Backup schedules."""

import abc
import dataclasses
from datetime import tzinfo

import voluptuous as vlp
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger

from yaesm.errors import YaesmValueError
from yaesm.names import name_valid


def schedule_name_valid(name: object) -> bool:
    """Return whether a name is safe to use in an artifact name."""
    return name_valid(name)


class ScheduleBase(abc.ABC):
    """Base class for ways a backup can be scheduled."""

    @classmethod
    @abc.abstractmethod
    def name(cls) -> str:
        """Return the stable configuration name for this schedule type."""

    @staticmethod
    @abc.abstractmethod
    def config_schema() -> vlp.Schema:
        """Return the complete schema for this schedule's configuration."""

    def timer_triggers(self, timezone: tzinfo | None = None) -> tuple[BaseTrigger, ...]:
        """Return timer triggers, or none for externally activated schedules."""
        return ()


@dataclasses.dataclass(frozen=True)
class OnDemandSchedule(ScheduleBase):
    """A schedule activated explicitly rather than by a timer."""

    @classmethod
    def name(cls) -> str:
        return "on-demand"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})


@dataclasses.dataclass(frozen=True)
class CronSchedule(ScheduleBase):
    """A schedule described by a standard five-field cron expression."""

    expression: str

    def __post_init__(self) -> None:
        if not isinstance(self.expression, str):
            raise YaesmValueError(f"invalid cron expression: {self.expression!r}")
        try:
            CronTrigger.from_crontab(self.expression)
        except ValueError as error:
            raise YaesmValueError(f"invalid cron expression: {self.expression!r}") from error

    @classmethod
    def name(cls) -> str:
        return "cron"

    @staticmethod
    def config_schema() -> vlp.Schema:
        def validate_expression(value: object) -> str:
            if not isinstance(value, str):
                raise vlp.Invalid("expression must be a string")
            try:
                CronTrigger.from_crontab(value)
            except ValueError as error:
                raise vlp.Invalid(f"invalid cron expression: {value!r}") from error
            return value

        mapping = vlp.Schema({vlp.Required("expression"): validate_expression})
        return vlp.Schema(
            lambda value: mapping({"expression": value} if isinstance(value, str) else value)
        )

    def timer_triggers(self, timezone: tzinfo | None = None) -> tuple[BaseTrigger, ...]:
        """Return the cron trigger."""
        return (CronTrigger.from_crontab(self.expression, timezone=timezone),)


@dataclasses.dataclass(frozen=True)
class Schedule:
    """A configured name paired with a schedule implementation."""

    name: str
    implementation: ScheduleBase
    previous_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not schedule_name_valid(self.name):
            raise YaesmValueError(f"invalid schedule name: {self.name!r}")
        seen = {self.name}
        for name in self.previous_names:
            if not schedule_name_valid(name):
                raise YaesmValueError(f"invalid previous schedule name: {name!r}")
            if name in seen:
                raise YaesmValueError(f"duplicate schedule name: {name!r}")
            seen.add(name)

    @property
    def names(self) -> tuple[str, ...]:
        """Return the current and previous schedule names."""
        return (self.name, *self.previous_names)

    def timer_triggers(self, timezone: tzinfo | None = None) -> tuple[BaseTrigger, ...]:
        """Return this schedule's timer triggers."""
        return self.implementation.timer_triggers(timezone)
