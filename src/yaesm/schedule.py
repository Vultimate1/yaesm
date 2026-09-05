"""Backup schedules."""

import abc
import dataclasses
from datetime import tzinfo

import voluptuous as vlp
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger

from yaesm.errors import YaesmValueError
from yaesm.names import validate_name


def validate_schedule_name(name: object) -> str:
    """Return a valid schedule name or raise with the reason it is invalid."""
    return validate_name(name)


def schedule_name_valid(name: object) -> bool:
    """Return whether a name is safe to use in an artifact name."""
    try:
        validate_schedule_name(name)
    except YaesmValueError:
        return False
    return True


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

        return vlp.Schema(lambda value: {"expression": validate_expression(value)})

    def timer_triggers(self, timezone: tzinfo | None = None) -> tuple[BaseTrigger, ...]:
        """Return the cron trigger."""
        return (CronTrigger.from_crontab(self.expression, timezone=timezone),)


@dataclasses.dataclass(frozen=True)
class Schedule:
    """A configured schedule name paired with its trigger."""

    name: str
    trigger: ScheduleBase
    previous_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            validate_schedule_name(self.name)
        except YaesmValueError as error:
            raise YaesmValueError(f"invalid schedule name: {self.name!r}") from error
        seen = {self.name}
        for name in self.previous_names:
            try:
                validate_schedule_name(name)
            except YaesmValueError as error:
                raise YaesmValueError(f"invalid previous schedule name: {name!r}") from error
            if name in seen:
                raise YaesmValueError(f"duplicate schedule name: {name!r}")
            seen.add(name)

    @property
    def names(self) -> tuple[str, ...]:
        """Return the current and previous schedule names."""
        return (self.name, *self.previous_names)

    def timer_triggers(self, timezone: tzinfo | None = None) -> tuple[BaseTrigger, ...]:
        """Return this schedule's timer triggers."""
        return self.trigger.timer_triggers(timezone)
