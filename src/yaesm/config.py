"""Configuration parsing and orchestration."""

import voluptuous as vlp

import yaesm.ty as ty
from yaesm.errors import YaesmError
from yaesm.retention import KeepFor, KeepLast, RetentionPolicyBase
from yaesm.schedule import CronSchedule, Schedule, ScheduleBase

_SCHEDULE_TYPES = {schedule.name(): schedule for schedule in (CronSchedule,)}
_RETENTION_TYPES = {policy.name(): policy for policy in (KeepLast, KeepFor)}


class ConfigError(YaesmError):
    """Raised when yaesm configuration is invalid."""


def parse_schedules(
    value: object,
) -> tuple[tuple[Schedule, ...], tuple[RetentionPolicyBase, ...]]:
    """Parse named schedules and their nested retention policies."""
    if not isinstance(value, dict):
        raise ConfigError("schedules must be a mapping")
    if not value:
        raise ConfigError("at least one schedule is required")

    schedules = []
    policies = []
    for schedule_name, definition in value.items():
        if not isinstance(schedule_name, str) or not schedule_name:
            raise ConfigError("schedule names must be nonempty strings")
        if not isinstance(definition, dict):
            raise ConfigError(f"schedule {schedule_name!r} must be a mapping")
        if "retention" not in definition:
            raise ConfigError(f"schedule {schedule_name!r} has no retention policy")

        implementations = tuple(
            (name, config) for name, config in definition.items() if name != "retention"
        )
        if len(implementations) != 1:
            raise ConfigError(f"schedule {schedule_name!r} must select one schedule type")
        type_name, config = implementations[0]
        schedule_type = _SCHEDULE_TYPES.get(type_name)
        if schedule_type is None:
            raise ConfigError(f"schedule {schedule_name!r} uses unknown type {type_name!r}")

        try:
            config = schedule_type.config_schema()(config)
        except vlp.Invalid as error:
            raise ConfigError(
                f"schedule {schedule_name!r} has invalid {type_name} configuration: {error}"
            ) from error
        implementation = ty.cast(ty.Callable[..., ScheduleBase], schedule_type)(**config)
        schedules.append(Schedule(schedule_name, implementation))
        policies.extend(_parse_retention(schedule_name, definition["retention"]))

    return tuple(schedules), tuple(policies)


def _parse_retention(
    schedule_name: str,
    value: object,
) -> tuple[RetentionPolicyBase, ...]:
    entries = value if isinstance(value, list) else [value]
    if not entries:
        raise ConfigError(f"schedule {schedule_name!r} has no retention policy")

    policies = []
    for entry in entries:
        if not isinstance(entry, dict) or len(entry) != 1:
            raise ConfigError(
                f"schedule {schedule_name!r} retention policies must select one policy type"
            )
        type_name, config = next(iter(entry.items()))
        policy_type = _RETENTION_TYPES.get(type_name)
        if policy_type is None:
            raise ConfigError(
                f"schedule {schedule_name!r} uses unknown retention policy {type_name!r}"
            )
        try:
            config = policy_type.config_schema()(config)
        except vlp.Invalid as error:
            raise ConfigError(
                f"schedule {schedule_name!r} has invalid {type_name} configuration: {error}"
            ) from error
        policy = ty.cast(ty.Callable[..., RetentionPolicyBase], policy_type)(
            **config,
            schedule_name=schedule_name,
        )
        policies.append(policy)
    return tuple(policies)
