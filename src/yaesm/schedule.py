"""Backup schedules."""

import abc
import dataclasses

import voluptuous as vlp
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger

from yaesm.errors import YaesmValueError


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

    def timer_triggers(self) -> tuple[BaseTrigger, ...]:
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

    def timer_triggers(self) -> tuple[BaseTrigger, ...]:
        """Return the cron trigger."""
        return (CronTrigger.from_crontab(self.expression),)


@dataclasses.dataclass(frozen=True)
class Schedule:
    """A configured name paired with a schedule implementation."""

    name: str
    implementation: ScheduleBase

    def timer_triggers(self) -> tuple[BaseTrigger, ...]:
        """Return this schedule's timer triggers."""
        return self.implementation.timer_triggers()
