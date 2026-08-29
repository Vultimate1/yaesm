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
from yaesm.retention import KeepAll, RetentionPolicyBase
from yaesm.schedule import OnDemandSchedule, Schedule, ScheduleBase, schedule_name_valid
from yaesm.ssh import SSHTarget, SSHTargetError


class _Named(ty.Protocol):
    @classmethod
    def name(cls) -> str: ...


_NamedType = ty.TypeVar("_NamedType", bound=_Named)

load_drivers()


class ConfigError(YaesmError):
    """Raised when yaesm configuration is invalid."""

    def __init__(self, messages: str | ty.Sequence[str]) -> None:
        self.messages = (messages,) if isinstance(messages, str) else tuple(messages)
        super().__init__(*self.messages)

    def format(self) -> str:
        """Format one error directly or multiple errors as a list."""
        if len(self.messages) == 1:
            return self.messages[0]
        return "configuration errors:\n" + "\n".join(f"  - {message}" for message in self.messages)

    def __str__(self) -> str:
        return self.format()


def _collect_messages(messages: list[str], error: YaesmError, prefix: str = "") -> None:
    errors = error.messages if isinstance(error, ConfigError) else (error.format(),)
    messages.extend(f"{prefix}{message}" for message in errors)


@dataclasses.dataclass(frozen=True)
class Config:
    """Parsed global settings and backups."""

    global_settings: GlobalSettings
    backups: dict[str, bckp.Backup]
    backups_by_name: dict[str, bckp.Backup] = dataclasses.field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        backups_by_name = {}
        for backup in self.backups.values():
            for name in backup.names:
                if owner := backups_by_name.get(name):
                    raise ConfigError(
                        f"backup name {name!r} is used by both {owner.name!r} and {backup.name!r}"
                    )
                backups_by_name[name] = backup
        object.__setattr__(self, "backups_by_name", backups_by_name)


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

    messages = []
    raw_global_settings = value.get("global_settings", {})
    if not isinstance(raw_global_settings, dict):
        messages.append("global_settings must be a mapping")
        global_settings = {}
    else:
        if any(not isinstance(name, str) for name in raw_global_settings):
            messages.append("global setting names must be strings")
        global_settings = {
            name: setting for name, setting in raw_global_settings.items() if isinstance(name, str)
        }

    backups = {}
    backup_names = {name for name in value if isinstance(name, str) and name != "global_settings"}
    has_backup_definition = any(name != "global_settings" for name in value)
    for name, definition in value.items():
        if name == "global_settings":
            continue
        if not isinstance(name, str):
            messages.append("backup names must be strings")
            continue
        try:
            backups[name] = _parse_backup(name, definition, global_settings)
        except YaesmError as error:
            _collect_messages(messages, error, f"backup {name!r}: ")

    config = None
    try:
        config = Config(global_settings, backups)
    except ConfigError as error:
        messages.extend(error.messages)
    if not has_backup_definition:
        messages.append("at least one backup is required")
    try:
        _validate_backup_sources(
            backups,
            backup_names,
            {} if config is None else config.backups_by_name,
        )
    except ConfigError as error:
        messages.extend(error.messages)
    if messages:
        raise ConfigError(messages)
    assert config is not None
    return config


def _parse_backup(
    name: str,
    value: object,
    global_settings: GlobalSettings,
) -> bckp.Backup:
    if not isinstance(value, dict):
        raise ConfigError("settings must be a mapping")

    messages = []
    required = {"source", "destination"}
    allowed = required | {
        "previous_names",
        "schedules",
        "skip_unchanged",
        "ssh",
        "transforms",
    }
    if missing := sorted(required - value.keys()):
        messages.append(f"missing required settings: {', '.join(missing)}")
    if unknown := sorted(value.keys() - allowed, key=str):
        messages.append(f"unknown settings: {', '.join(str(item) for item in unknown)}")

    try:
        previous_names = _parse_previous_names(
            name,
            value.get("previous_names", []),
            bckp.backup_name_valid,
            "backup",
        )
    except ConfigError as error:
        _collect_messages(messages, error)
        previous_names = ()

    ssh = None
    if "ssh" in value:
        try:
            ssh = SSHTarget.from_config(value["ssh"])
        except SSHTargetError as error:
            messages.append(error.format())

    source = None
    if "source" in value:
        try:
            source = _parse_source(value["source"], global_settings, ssh)
        except YaesmError as error:
            _collect_messages(messages, error)

    destination = None
    if "destination" in value:
        try:
            destination = _parse_driver(
                value["destination"],
                "destination",
                global_settings,
                ssh,
            )
            _validate_destination(destination)
        except YaesmError as error:
            _collect_messages(messages, error)

    try:
        transforms = _parse_transforms(value.get("transforms", []), global_settings, ssh)
    except YaesmError as error:
        _collect_messages(messages, error)
        transforms = ()

    try:
        schedules, retention = parse_schedules(value.get("schedules", {}))
    except YaesmError as error:
        _collect_messages(messages, error)
        schedules = ()
        retention = ()

    skip_unchanged = value.get("skip_unchanged", False)
    if not isinstance(skip_unchanged, bool):
        messages.append("skip_unchanged must be a boolean")

    if messages:
        raise ConfigError(messages)
    assert source is not None and destination is not None
    backup = bckp.Backup(
        name,
        source,
        destination,
        transforms,
        schedules,
        retention,
        previous_names=previous_names,
        skip_unchanged=skip_unchanged,
    )

    if isinstance(source, DriverBase):
        Pipeline(source, destination, transforms)
    return backup


def _parse_source(
    value: object,
    global_settings: GlobalSettings,
    ssh: SSHTarget | None,
) -> DriverBase | bckp.BackupSource:
    if isinstance(value, dict) and set(value) == {"backup"}:
        backup_name = value["backup"]
        if not isinstance(backup_name, str) or not backup_name:
            raise ConfigError("source backup name must be a nonempty string")
        return bckp.BackupSource(backup_name)
    return _parse_driver(value, "source", global_settings, ssh)


def _parse_transforms(
    value: object,
    global_settings: GlobalSettings,
    ssh: SSHTarget | None,
) -> tuple[DriverBase, ...]:
    if not isinstance(value, list):
        raise ConfigError("transforms must be a list")
    transforms = []
    messages = []
    for index, definition in enumerate(value, start=1):
        try:
            transforms.append(_parse_driver(definition, f"transform {index}", global_settings, ssh))
        except YaesmError as error:
            _collect_messages(messages, error)
    if messages:
        raise ConfigError(messages)
    return tuple(transforms)


def _parse_driver(
    value: object,
    label: str,
    global_settings: GlobalSettings,
    ssh: SSHTarget | None,
) -> DriverBase:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must select one driver")
    remote = value.get("remote", False)
    selection = tuple((name, config) for name, config in value.items() if name != "remote")
    if len(selection) != 1:
        raise ConfigError(f"{label} must select one driver")
    name, config = selection[0]
    driver_type = _type_named(DriverBase, name)
    if driver_type is None:
        raise ConfigError(f"{label} uses unknown driver {name!r}")

    try:
        if not isinstance(remote, bool):
            raise vlp.Invalid("remote must be a boolean")
        if remote and ssh is None:
            raise vlp.Invalid("remote requires backup SSH configuration")
        parsed = driver_type.config_schema()(config)
    except vlp.Invalid as error:
        raise ConfigError(f"{label} has invalid {name} configuration: {error}") from error
    constructor = ty.cast(ty.Callable[..., DriverBase], driver_type)
    if remote:
        parsed["ssh"] = ssh
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


def _validate_backup_sources(
    backups: ty.Mapping[str, bckp.Backup],
    declared_names: set[str],
    backups_by_name: ty.Mapping[str, bckp.Backup],
) -> None:
    messages = []
    visiting = []
    visited = set()

    def visit(name: str) -> None:
        if name in visiting:
            cycle = (*visiting[visiting.index(name) :], name)
            messages.append(f"backup source cycle: {' -> '.join(cycle)}")
            return
        if name in visited:
            return
        visiting.append(name)
        source = backups[name].source
        if isinstance(source, bckp.BackupSource):
            source_name = source.backup_name
            source_backup = backups_by_name.get(source_name)
            if source_backup is not None:
                visit(source_backup.name)
                source_ssh = source_backup.destination.ssh
                backup_ssh = next(
                    (
                        driver.ssh
                        for driver in (backups[name].destination, *backups[name].transforms)
                        if driver.ssh is not None
                    ),
                    None,
                )
                if source_ssh is not None and backup_ssh is not None and source_ssh != backup_ssh:
                    messages.append(
                        f"backup {name!r} and source backup {source_backup.name!r} "
                        "use different SSH configurations"
                    )
            elif source_name not in declared_names:
                messages.append(f"backup {name!r} references unknown source backup {source_name!r}")
        visiting.pop()
        visited.add(name)

    for name in backups:
        visit(name)
    if messages:
        raise ConfigError(messages)


def parse_schedules(
    value: object,
) -> tuple[tuple[Schedule, ...], tuple[RetentionPolicyBase, ...]]:
    """Parse named schedules and their nested retention policies."""
    if not isinstance(value, dict):
        raise ConfigError("schedules must be a mapping")

    schedules = []
    policies = []
    messages = []
    for schedule_name, definition in value.items():
        try:
            if not schedule_name_valid(schedule_name):
                raise ConfigError(f"invalid schedule name: {schedule_name!r}")
            schedule, retention = _parse_schedule(schedule_name, definition)
            if schedule_name == "manual" and not isinstance(
                schedule.implementation, OnDemandSchedule
            ):
                raise ConfigError("schedule 'manual' must be on-demand")
            schedules.append(schedule)
            policies.extend(retention)
        except YaesmError as error:
            _collect_messages(messages, error)

    if messages:
        raise ConfigError(messages)
    if not any(isinstance(schedule.implementation, OnDemandSchedule) for schedule in schedules):
        schedules.append(Schedule("manual", OnDemandSchedule()))
        policies.append(KeepAll("manual"))
    return tuple(schedules), tuple(policies)


def _parse_schedule(
    schedule_name: str,
    value: object,
) -> tuple[Schedule, tuple[RetentionPolicyBase, ...]]:
    if not isinstance(value, dict):
        raise ConfigError(f"schedule {schedule_name!r} must be a mapping")
    if "retention" not in value:
        raise ConfigError(f"schedule {schedule_name!r} has no retention policy")

    previous_names = _parse_previous_names(
        schedule_name,
        value.get("previous_names", []),
        schedule_name_valid,
        "schedule",
    )
    implementations = tuple(
        (name, config)
        for name, config in value.items()
        if name not in {"previous_names", "retention"}
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
    return Schedule(
        schedule_name,
        implementation,
        previous_names=previous_names,
    ), _parse_retention(schedule_name, value["retention"])


def _parse_previous_names(
    current_name: str,
    value: object,
    valid: ty.Callable[[object], bool],
    kind: str,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigError("previous_names must be a list")
    names = tuple(value)
    seen = {current_name}
    for name in names:
        if not valid(name):
            raise ConfigError(f"invalid previous {kind} name: {name!r}")
        if name in seen:
            raise ConfigError(f"duplicate {kind} name: {name!r}")
        seen.add(name)
    return ty.cast(tuple[str, ...], names)


def _parse_retention(
    schedule_name: str,
    value: object,
) -> tuple[RetentionPolicyBase, ...]:
    entries = value if isinstance(value, list) else [value]
    if not entries:
        raise ConfigError(f"schedule {schedule_name!r} has no retention policy")

    policies = []
    messages = []
    for entry in entries:
        try:
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
        except YaesmError as error:
            _collect_messages(messages, error)
    if messages:
        raise ConfigError(messages)
    return tuple(policies)


def _type_named(base: type[_NamedType], name: object) -> type[_NamedType] | None:
    for implementation in base.__subclasses__():
        if not inspect.isabstract(implementation) and implementation.name() == name:
            return implementation
        if found := _type_named(implementation, name):
            return found
    return None
