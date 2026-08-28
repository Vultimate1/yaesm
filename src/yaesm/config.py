"""Configuration parsing and orchestration."""

import dataclasses
import inspect
from pathlib import Path

import voluptuous as vlp
import yaml

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm.driver import load_drivers
from yaesm.driver.driverbase import DriverBase, GlobalSettings
from yaesm.errors import YaesmError
from yaesm.pipeline import Pipeline
from yaesm.representation import DataProperty
from yaesm.retention import RetentionPolicyBase
from yaesm.schedule import Schedule, ScheduleBase
from yaesm.ssh import SSHTarget, SSHTargetError


class _Named(ty.Protocol):
    @classmethod
    def name(cls) -> str: ...


_NamedType = ty.TypeVar("_NamedType", bound=_Named)

load_drivers()


class ConfigError(YaesmError):
    """Raised when yaesm configuration is invalid."""


@dataclasses.dataclass(frozen=True)
class Config:
    """Parsed global settings and backups."""

    global_settings: GlobalSettings
    backups: dict[str, bckp.Backup]


def parse_config(value: object) -> Config:
    """Parse a YAML file or configuration data."""
    if isinstance(value, str | Path):
        path = Path(value)
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ConfigError(f"could not read configuration file {path}: {error}") from error
        except yaml.YAMLError as error:
            raise ConfigError(f"invalid YAML in configuration file {path}: {error}") from error

    if not isinstance(value, dict):
        raise ConfigError("configuration must be a mapping")

    global_settings = value.get("global_settings", {})
    if not isinstance(global_settings, dict):
        raise ConfigError("global_settings must be a mapping")
    if any(not isinstance(name, str) for name in global_settings):
        raise ConfigError("global setting names must be strings")
    global_settings = ty.cast(dict[str, object], dict(global_settings))

    backups = {}
    for name, definition in value.items():
        if name == "global_settings":
            continue
        if not isinstance(name, str):
            raise ConfigError("backup names must be strings")
        try:
            backups[name] = _parse_backup(name, definition, global_settings)
        except YaesmError as error:
            raise ConfigError(f"backup {name!r}: {error}") from error

    if not backups:
        raise ConfigError("at least one backup is required")
    _validate_backup_sources(backups)
    return Config(global_settings, backups)


def _parse_backup(
    name: str,
    value: object,
    global_settings: GlobalSettings,
) -> bckp.Backup:
    if not isinstance(value, dict):
        raise ConfigError("settings must be a mapping")

    required = {"source", "destination", "schedules"}
    allowed = required | {"drivers", "requirements"}
    if missing := sorted(required - value.keys()):
        raise ConfigError(f"missing required settings: {', '.join(missing)}")
    if unknown := sorted(value.keys() - allowed, key=str):
        raise ConfigError(f"unknown settings: {', '.join(str(item) for item in unknown)}")

    source = _parse_source(value["source"], global_settings)
    destination = _parse_driver(value["destination"], "destination", global_settings)
    _validate_destination(destination)
    drivers = _parse_drivers(value.get("drivers", []), global_settings)
    requirements = _parse_requirements(value.get("requirements", []))
    schedules, retention = parse_schedules(value["schedules"])
    backup = bckp.Backup(
        name,
        source,
        destination,
        drivers,
        requirements,
        schedules,
        retention,
    )

    if isinstance(source, bckp.DriverSource):
        Pipeline(source, destination, drivers, requirements)
    return backup


def _parse_source(
    value: object,
    global_settings: GlobalSettings,
) -> bckp.DriverSource | bckp.BackupSource:
    if isinstance(value, dict) and set(value) == {"backup"}:
        backup_name = value["backup"]
        if not isinstance(backup_name, str) or not backup_name:
            raise ConfigError("source backup name must be a nonempty string")
        return bckp.BackupSource(backup_name)
    return bckp.DriverSource(_parse_driver(value, "source", global_settings))


def _parse_drivers(
    value: object,
    global_settings: GlobalSettings,
) -> tuple[DriverBase, ...]:
    if not isinstance(value, list):
        raise ConfigError("drivers must be a list")
    return tuple(
        _parse_driver(definition, f"driver {index}", global_settings)
        for index, definition in enumerate(value, start=1)
    )


def _parse_driver(
    value: object,
    label: str,
    global_settings: GlobalSettings,
) -> DriverBase:
    if not isinstance(value, dict) or len(value) != 1:
        raise ConfigError(f"{label} must select one driver")
    name, config = next(iter(value.items()))
    driver_type = _type_named(DriverBase, name)
    if driver_type is None:
        raise ConfigError(f"{label} uses unknown driver {name!r}")

    try:
        if isinstance(config, dict) and "target" in config:
            config = dict(config)
            config["target"] = SSHTarget.from_config(config["target"])
        parsed = driver_type.config_schema()(config)
    except (vlp.Invalid, SSHTargetError) as error:
        raise ConfigError(f"{label} has invalid {name} configuration: {error}") from error
    constructor = ty.cast(ty.Callable[..., DriverBase], driver_type)
    return constructor(**parsed, global_settings=global_settings)


def _validate_destination(driver: DriverBase) -> None:
    capabilities = driver.capabilities()
    missing = {"list", "delete"} - capabilities
    if missing:
        raise ConfigError(
            f"destination driver {driver.name()} does not provide: {', '.join(sorted(missing))}"
        )
    if not {"store", "import"} & driver.pipeline_capabilities():
        raise ConfigError(f"destination driver {driver.name()} cannot store backup artifacts")


def _parse_requirements(value: object) -> frozenset[DataProperty]:
    if not isinstance(value, list):
        raise ConfigError("requirements must be a list")
    try:
        return frozenset(DataProperty(requirement) for requirement in value)
    except (TypeError, ValueError) as error:
        raise ConfigError(f"unknown data requirement: {error}") from error


def _validate_backup_sources(backups: ty.Mapping[str, bckp.Backup]) -> None:
    visiting = []
    visited = set()

    def visit(name: str) -> None:
        if name in visiting:
            cycle = (*visiting[visiting.index(name) :], name)
            raise ConfigError(f"backup source cycle: {' -> '.join(cycle)}")
        if name in visited:
            return
        visiting.append(name)
        source = backups[name].source
        if isinstance(source, bckp.BackupSource):
            source_name = source.backup_name
            if source_name not in backups:
                raise ConfigError(
                    f"backup {name!r} references unknown source backup {source_name!r}"
                )
            visit(source_name)
        visiting.pop()
        visited.add(name)

    for name in backups:
        visit(name)


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
        schedule_type = _type_named(ScheduleBase, type_name)
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
        policy_type = _type_named(RetentionPolicyBase, type_name)
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


def _type_named(base: type[_NamedType], name: object) -> type[_NamedType] | None:
    for implementation in base.__subclasses__():
        if not inspect.isabstract(implementation) and implementation.name() == name:
            return implementation
        if found := _type_named(implementation, name):
            return found
    return None
