"""Tests for yaesm.config."""

import inspect
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import voluptuous as vlp
from hypothesis import given
from hypothesis import strategies as st

import yaesm.backup as bckp
import yaesm.config as config_module
import yaesm.ty as ty
from yaesm.config import (
    BackupGroup,
    BackupTargetError,
    Config,
    ConfigError,
    parse_config,
    parse_schedules,
)
from yaesm.driver.btrfsdriver import BtrfsDriver
from yaesm.driver.driverbase import DriverBase
from yaesm.driver.gpgdriver import GPGDriver
from yaesm.driver.rsyncdriver import RsyncDriver
from yaesm.driver.tardriver import TarDriver
from yaesm.driver.zfsdriver import ZFSDriver
from yaesm.driver.zstddriver import ZstdDriver
from yaesm.names import ALL_TARGET_NAME
from yaesm.pipeline import Pipeline
from yaesm.representation import Representation
from yaesm.retention import KeepAll, KeepFor, KeepLast, RetentionPolicyBase
from yaesm.schedule import CronSchedule, OnDemandSchedule, Schedule, ScheduleBase
from yaesm.ssh import SSHTarget


class AutomaticallyDiscoveredDriver(BtrfsDriver):
    @classmethod
    def name(cls) -> str:
        return "automatically-discovered"


class AutomaticallyDiscoveredSchedule(ScheduleBase):
    @classmethod
    def name(cls) -> str:
        return "automatically-discovered"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})


class AutomaticallyDiscoveredRetention(KeepLast):
    @classmethod
    def name(cls) -> str:
        return "automatically-discovered"


class UnstorableDriver(DriverBase):
    @classmethod
    def name(cls) -> str:
        return "unstorable"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})

    def cap_list(self, backup_name: str) -> ty.Sequence[bckp.BackupArtifact[Representation]]:
        return ()

    def cap_delete(
        self,
        artifacts: ty.Sequence[bckp.BackupArtifact[Representation]],
    ) -> None:
        pass


def backup_config(**settings):
    config = {
        "source": {"btrfs": "/source"},
        "destination": {"btrfs": "/destination"},
        "schedules": {
            "daily": {
                "cron": "30 4 * * *",
                "retention": {"keep-last": 7},
            }
        },
    }
    config.update(settings)
    return config


_BACKUP_NAMES = st.from_regex(r"[a-z0-9_][a-z0-9_-]{0,12}", fullmatch=True).filter(
    lambda name: name != "settings" and not name.startswith("old-")
)
_SCHEDULE_NAMES = st.from_regex(r"[a-z0-9_][a-z0-9_-]{0,8}", fullmatch=True).filter(
    lambda name: name != "manual" and not name.startswith("old-")
)
_PATHS = st.sampled_from(("/source", "/home", "/srv/data", "/srv/backup data"))
_DATASETS = st.sampled_from(("tank/source", "tank/home", "backup/archive"))
_BTRFS_CONFIGS = st.one_of(
    _PATHS,
    st.builds(lambda location: {"location": location}, _PATHS),
)
_RSYNC_CONFIGS = st.one_of(
    _PATHS,
    st.builds(
        lambda location, options, exclude, one_file_system: {
            "location": location,
            "extra_options": options,
            "exclude": exclude,
            "one_file_system": one_file_system,
        },
        _PATHS,
        st.one_of(
            st.sampled_from(("--checksum", "--bwlimit=1000", "--delete-delay")),
            st.lists(
                st.sampled_from(("--checksum", "--bwlimit=1000", "--delete-delay")),
                max_size=3,
            ),
        ),
        st.one_of(
            st.sampled_from((".cache/", "*.tmp")),
            st.lists(st.sampled_from((".cache/", "*.tmp", "build/")), max_size=3),
        ),
        st.booleans(),
    ),
)
_ZFS_CONFIGS = st.one_of(
    _DATASETS,
    st.builds(
        lambda dataset, encryption: {"dataset": dataset, "encryption": encryption},
        _DATASETS,
        st.booleans(),
    ),
)
_TAR_CONFIGS = st.one_of(
    _PATHS,
    st.builds(
        lambda location, one_file_system: {
            "location": location,
            "one_file_system": one_file_system,
        },
        _PATHS,
        st.booleans(),
    ),
)
_GPG_CONFIGS = st.one_of(
    st.sampled_from(("/key.asc", "/root/backup key.asc")),
    st.sampled_from(("/key.asc", "/root/backup key.asc")).map(
        lambda public_key: {"public_key": public_key}
    ),
)
_ZSTD_CONFIGS = st.one_of(
    st.just({}),
    st.integers(min_value=1, max_value=19),
    st.integers(min_value=1, max_value=19).map(lambda level: {"level": level}),
)
_DRIVER_CONFIGS = {
    "btrfs": _BTRFS_CONFIGS,
    "rsync": _RSYNC_CONFIGS,
    "zfs": _ZFS_CONFIGS,
    "tar": _TAR_CONFIGS,
    "gpg": _GPG_CONFIGS,
    "zstd": _ZSTD_CONFIGS,
}
_CRON_EXPRESSIONS = st.sampled_from(("* * * * *", "0 * * * *", "30 4 * * *", "*/15 9-17 * * 1-5"))
_RETENTION = st.one_of(
    st.just({"keep-all": {}}),
    st.integers(min_value=1, max_value=100).map(lambda count: {"keep-last": count}),
    st.integers(min_value=1, max_value=100).map(lambda count: {"keep-last": {"count": count}}),
    st.tuples(
        st.integers(min_value=1, max_value=100),
        st.sampled_from(("s", "m", "h", "d", "w", "y")),
    ).map(lambda duration: {"keep-for": f"{duration[0]}{duration[1]}"}),
    st.timedeltas(min_value=timedelta(seconds=1), max_value=timedelta(days=3650)).map(
        lambda duration: {"keep-for": {"duration": duration}}
    ),
)
_RETENTIONS = st.one_of(_RETENTION, st.lists(_RETENTION, min_size=1, max_size=3))
_SSH_CONFIGS = st.builds(
    lambda endpoint, identity_file, config_file: {
        "endpoint": endpoint,
        "identity_file": identity_file,
        **({} if config_file is None else {"config_file": config_file}),
    },
    st.sampled_from(("ssh://server", "ssh://backup@server:2222", "ssh://[2001:db8::1]")),
    st.sampled_from(("/root/.ssh/id_ed25519", "/root/.ssh/backup key")),
    st.none() | st.just("/root/.ssh/config"),
)
_SSH_DEFAULTS = st.builds(
    lambda identity_file, config_file: {
        **({} if identity_file is None else {"identity_file": identity_file}),
        **({} if config_file is None else {"config_file": config_file}),
    },
    st.none() | st.just("/root/.ssh/id_ed25519"),
    st.none() | st.just("/root/.ssh/config"),
)
_GLOBAL_SETTINGS = st.builds(
    lambda max_concurrent_backups, timezone, defaults: {
        **(
            {}
            if max_concurrent_backups is None and timezone is None
            else {
                "scheduler": {
                    **(
                        {}
                        if max_concurrent_backups is None
                        else {"max_concurrent_backups": max_concurrent_backups}
                    ),
                    **({} if timezone is None else {"timezone": timezone}),
                }
            }
        ),
        **({} if defaults is None else {"ssh": defaults}),
    },
    st.none() | st.integers(min_value=1, max_value=100),
    st.none() | st.sampled_from(("UTC", "America/New_York")),
    st.none() | _SSH_DEFAULTS,
)


@st.composite
def driver_definitions(draw, name, remote_allowed):
    definition = {name: draw(_DRIVER_CONFIGS[name])}
    if draw(st.booleans()):
        definition["remote"] = draw(st.booleans()) if remote_allowed else False
    return definition


@st.composite
def schedule_definitions(draw):
    if not draw(st.booleans()):
        return None

    names = draw(st.lists(_SCHEDULE_NAMES, max_size=3, unique=True))
    schedules = {}
    for name in names:
        if draw(st.booleans()):
            expression = draw(_CRON_EXPRESSIONS)
            implementation = {
                "cron": expression if draw(st.booleans()) else {"expression": expression}
            }
        else:
            implementation = {"on-demand": {}}
        schedules[name] = {
            **implementation,
            "retention": draw(_RETENTIONS),
            **({"previous_names": [f"old-{name}"]} if draw(st.booleans()) else {}),
        }

    if draw(st.booleans()):
        schedules["manual"] = {
            "on-demand": {},
            "retention": draw(_RETENTIONS),
        }
    return schedules


@st.composite
def direct_pipeline_definitions(draw, remote_allowed):
    if draw(st.booleans()):
        driver_name = draw(st.sampled_from(("btrfs", "rsync", "zfs")))
        source = draw(driver_definitions(driver_name, remote_allowed))
        destination = draw(driver_definitions(driver_name, remote_allowed))
        transforms = []
    else:
        driver_name = draw(st.sampled_from(("btrfs", "rsync")))
        source = draw(driver_definitions(driver_name, remote_allowed))
        destination = draw(driver_definitions("tar", remote_allowed))
        transform_names = draw(
            st.sampled_from(((), ("zstd",), ("gpg",), ("zstd", "gpg"), ("gpg", "zstd")))
        )
        transforms = [draw(driver_definitions(name, remote_allowed)) for name in transform_names]
    return source, destination, transforms, next(name for name in destination if name != "remote")


@st.composite
def valid_backup_definitions(draw, name, ssh, previous_backups):
    use_ssh = draw(st.booleans())
    remote_allowed = use_ssh
    replication_sources = tuple(
        (alias, driver_name)
        for aliases, driver_name in previous_backups
        if driver_name != "tar"
        for alias in aliases
    )
    if replication_sources and draw(st.booleans()):
        source_name, destination_name = draw(st.sampled_from(replication_sources))
        source = {"backup": source_name}
        destination = draw(driver_definitions(destination_name, remote_allowed))
        transforms = []
    else:
        source, destination, transforms, destination_name = draw(
            direct_pipeline_definitions(remote_allowed)
        )

    previous_names = [f"old-{name}"] if draw(st.booleans()) else []
    definition = {
        "source": source,
        "destination": destination,
        **({"transforms": transforms} if transforms or draw(st.booleans()) else {}),
        **({"previous_names": previous_names} if previous_names else {}),
        **({"ssh": ssh} if use_ssh else {}),
    }
    if draw(st.booleans()):
        supported = "backup" in source or destination_name in {"btrfs", "zfs"}
        definition["skip_unchanged"] = draw(st.booleans()) if supported else False
    schedules = draw(schedule_definitions())
    if schedules is not None:
        definition["schedules"] = schedules
    return definition, ((name, *previous_names), destination_name)


@st.composite
def valid_configs(draw):
    backup_count = draw(st.integers(min_value=1, max_value=5))
    group_count = draw(st.integers(min_value=0, max_value=4))
    names = draw(
        st.lists(
            _BACKUP_NAMES,
            min_size=backup_count + group_count,
            max_size=backup_count + group_count,
            unique=True,
        )
    )
    backup_names = names[:backup_count]
    group_names = names[backup_count:]
    ssh = draw(_SSH_CONFIGS)
    config = {}
    previous_backups = []
    for name in backup_names:
        definition, backup = draw(valid_backup_definitions(name, ssh, previous_backups))
        config[name] = definition
        previous_backups.append(backup)

    available_targets = [alias for aliases, _driver in previous_backups for alias in aliases]
    for name in group_names:
        members = draw(
            st.lists(
                st.sampled_from(available_targets),
                min_size=1,
                max_size=min(6, len(available_targets) + 2),
            )
        )
        config[name] = {"group": members}
        available_targets.append(name)
    if draw(st.booleans()):
        config["settings"] = draw(_GLOBAL_SETTINGS)
    entry_order = draw(st.permutations(tuple(config)))
    return {name: config[name] for name in entry_order}


_INVALID_MUTATION_NAMES = (
    "nonmapping-backup",
    "missing-source",
    "missing-destination",
    "missing-source-and-destination",
    "unknown-setting",
    "invalid-skip-unchanged",
    "unsupported-skip-unchanged",
    "invalid-previous-names",
    "invalid-ssh",
    "empty-source-selection",
    "multiple-source-selection",
    "unknown-source",
    "invalid-source-configuration",
    "empty-destination-selection",
    "unknown-destination",
    "invalid-destination-configuration",
    "nonlist-transforms",
    "invalid-transform-selection",
    "unknown-transform",
    "invalid-transform-configuration",
    "nonmapping-schedules",
    "invalid-schedule-name",
    "nonmapping-schedule",
    "missing-retention",
    "multiple-schedule-types",
    "unknown-schedule-type",
    "invalid-schedule-configuration",
    "invalid-previous-schedule-names",
    "empty-retention",
    "invalid-retention-selection",
    "unknown-retention",
    "invalid-keep-last",
    "invalid-keep-for",
    "invalid-keep-all",
)
_INVALID_MUTATIONS = st.sampled_from(_INVALID_MUTATION_NAMES)


def invalidate_backup(config, mutation):
    schedule = config["schedules"]["daily"]
    match mutation:
        case "nonmapping-backup":
            return [], "settings must be a mapping"
        case "missing-source":
            del config["source"]
            return config, "missing required settings: source"
        case "missing-destination":
            del config["destination"]
            return config, "missing required settings: destination"
        case "missing-source-and-destination":
            del config["source"], config["destination"]
            return config, "missing required settings: destination, source"
        case "unknown-setting":
            config["unexpected"] = True
            return config, "unknown settings: unexpected"
        case "invalid-skip-unchanged":
            config["skip_unchanged"] = "yes"
            return config, "skip_unchanged must be a boolean"
        case "unsupported-skip-unchanged":
            config["skip_unchanged"] = True
            config["destination"] = {"tar": "/destination"}
            return config, "destination driver tar does not support skip_unchanged"
        case "invalid-previous-names":
            config["previous_names"] = "old-home"
            return config, "previous_names must be a list"
        case "invalid-ssh":
            config["ssh"] = {"endpoint": "server", "identity_file": "/key"}
            return config, "invalid SSH configuration"
        case "empty-source-selection":
            config["source"] = {}
            return config, "source must select one driver"
        case "multiple-source-selection":
            config["source"] = {"btrfs": "/source", "rsync": "/source"}
            return config, "source must select one driver"
        case "unknown-source":
            config["source"] = {"unknown": {}}
            return config, "source uses unknown driver 'unknown'"
        case "invalid-source-configuration":
            config["source"] = {"btrfs": "relative"}
            return config, "source has invalid btrfs configuration"
        case "empty-destination-selection":
            config["destination"] = {}
            return config, "destination must select one driver"
        case "unknown-destination":
            config["destination"] = {"unknown": {}}
            return config, "destination uses unknown driver 'unknown'"
        case "invalid-destination-configuration":
            config["destination"] = {"btrfs": "relative"}
            return config, "destination has invalid btrfs configuration"
        case "nonlist-transforms":
            config["transforms"] = {}
            return config, "transforms must be a list"
        case "invalid-transform-selection":
            config["transforms"] = [None]
            return config, "transform 1 must select one driver"
        case "unknown-transform":
            config["transforms"] = [{"unknown": {}}]
            return config, "transform 1 uses unknown driver 'unknown'"
        case "invalid-transform-configuration":
            config["transforms"] = [{"zstd": 20}]
            return config, "transform 1 has invalid zstd configuration"
        case "nonmapping-schedules":
            config["schedules"] = []
            return config, "schedules must be a mapping"
        case "invalid-schedule-name":
            config["schedules"] = {"bad name": schedule}
            return config, "invalid schedule name: 'bad name'"
        case "nonmapping-schedule":
            config["schedules"] = {"daily": []}
            return config, "schedule 'daily' must be a mapping"
        case "missing-retention":
            del schedule["retention"]
            return config, "schedule 'daily' has no retention policy"
        case "multiple-schedule-types":
            schedule["on-demand"] = {}
            return config, "schedule 'daily' must select one schedule type"
        case "unknown-schedule-type":
            del schedule["cron"]
            schedule["unknown"] = {}
            return config, "schedule 'daily' uses unknown type 'unknown'"
        case "invalid-schedule-configuration":
            schedule["cron"] = "invalid"
            return config, "schedule 'daily' has invalid cron configuration"
        case "invalid-previous-schedule-names":
            schedule["previous_names"] = "old-daily"
            return config, "previous_names must be a list"
        case "empty-retention":
            schedule["retention"] = []
            return config, "schedule 'daily' has no retention policy"
        case "invalid-retention-selection":
            schedule["retention"] = {}
            return config, "retention policies must select one policy type"
        case "unknown-retention":
            schedule["retention"] = {"unknown": 1}
            return config, "uses unknown retention policy 'unknown'"
        case "invalid-keep-last":
            schedule["retention"] = {"keep-last": 0}
            return config, "has invalid keep-last configuration"
        case "invalid-keep-for":
            schedule["retention"] = {"keep-for": "0d"}
            return config, "has invalid keep-for configuration"
        case "invalid-keep-all":
            schedule["retention"] = {"keep-all": True}
            return config, "has invalid keep-all configuration"
    raise AssertionError(f"unknown mutation: {mutation}")


@st.composite
def invalid_configs(draw):
    names = draw(st.lists(_BACKUP_NAMES, min_size=2, max_size=5, unique=True))
    config = {}
    messages = []
    global_error = draw(st.sampled_from((None, "nonmapping", "nonstring-name")))
    if global_error == "nonmapping":
        config["settings"] = []
        messages.append("settings must be a mapping")
    elif global_error == "nonstring-name":
        config["settings"] = {1: "value"}
        messages.append("setting names must be strings")
    for name in names:
        definition, message = invalidate_backup(backup_config(), draw(_INVALID_MUTATIONS))
        config[name] = definition
        messages.append(message)
    return config, tuple(messages)


_INVALID_GROUP_MUTATION_NAMES = (
    "nonlist-group",
    "empty-group",
    "unknown-setting",
    "nonstring-member",
    "invalid-member",
    "unknown-member",
    "invalid-name",
    "cycle",
    "backup-alias-collision",
    "group-source",
    "no-backups",
)
_INVALID_GROUP_MUTATIONS = st.sampled_from(_INVALID_GROUP_MUTATION_NAMES)


def invalid_group_config(names, mutation):
    backup_name, group_name, other_group_name, unknown_name = names
    config = {backup_name: backup_config()}

    match mutation:
        case "nonlist-group":
            config[group_name] = {"group": backup_name}
            message = "group must be a list"
        case "empty-group":
            config[group_name] = {"group": []}
            message = "group must contain at least one target"
        case "unknown-setting":
            config[group_name] = {"group": [backup_name], "unexpected": True}
            message = "unknown settings: unexpected"
        case "nonstring-member":
            config[group_name] = {"group": [1]}
            message = "group members must be strings"
        case "invalid-member":
            config[group_name] = {"group": ["bad/name"]}
            message = "invalid group member name: 'bad/name'"
        case "unknown-member":
            config[group_name] = {"group": [unknown_name]}
            message = f"group {group_name!r} references unknown target {unknown_name!r}"
        case "invalid-name":
            invalid_name = f"-{group_name}"
            config[invalid_name] = {"group": [backup_name]}
            message = f"invalid group name: {invalid_name!r}"
        case "cycle":
            config[group_name] = {"group": [other_group_name]}
            config[other_group_name] = {"group": [group_name]}
            message = f"backup group cycle: {group_name} -> {other_group_name} -> {group_name}"
        case "backup-alias-collision":
            alias = f"old-{backup_name}"
            config[backup_name] = backup_config(previous_names=[alias])
            config[alias] = {"group": [backup_name]}
            message = (
                f"target name {alias!r} is used by both backup {backup_name!r} and group {alias!r}"
            )
        case "group-source":
            config[group_name] = {"group": [backup_name]}
            config[other_group_name] = backup_config(source={"backup": group_name})
            message = (
                f"backup {other_group_name!r} references group {group_name!r} as its source; "
                "backup sources must reference a backup"
            )
        case "no-backups":
            config = {group_name: {"group": [group_name]}}
            message = "at least one backup is required"
        case _:
            raise AssertionError(f"unknown group mutation: {mutation}")
    return config, message


@st.composite
def invalid_group_configs(draw):
    names = draw(
        st.lists(
            _BACKUP_NAMES,
            min_size=4,
            max_size=4,
            unique=True,
        )
    )
    return invalid_group_config(names, draw(_INVALID_GROUP_MUTATIONS))


def test_config_types_are_discovered_from_subclasses():
    backup = parse_config(
        {
            "home": backup_config(
                source={"automatically-discovered": {"location": "/source"}},
                destination={"automatically-discovered": {"location": "/destination"}},
                schedules={
                    "automatic": {
                        "automatically-discovered": {},
                        "retention": {"automatically-discovered": 2},
                    }
                },
            )
        }
    ).backups["home"]

    assert isinstance(backup.source, AutomaticallyDiscoveredDriver)
    assert isinstance(backup.destination, AutomaticallyDiscoveredDriver)
    assert isinstance(backup.schedules[0].implementation, AutomaticallyDiscoveredSchedule)
    assert backup.schedules[1] == Schedule("manual", OnDemandSchedule())
    assert backup.retention_policies == (
        AutomaticallyDiscoveredRetention(2, "automatic"),
        KeepAll("manual"),
    )


def test_config_has_no_hardcoded_type_registries():
    assert not hasattr(config_module, "_DRIVER_TYPES")
    assert not hasattr(config_module, "_SCHEDULE_TYPES")
    assert not hasattr(config_module, "_RETENTION_TYPES")


def test_config_generator_covers_every_builtin_type():
    def names(base):
        implementations = set()
        for subclass in base.__subclasses__():
            if subclass.__module__.startswith("yaesm.") and not inspect.isabstract(subclass):
                implementations.add(subclass.name())
            implementations.update(names(subclass))
        return implementations

    assert set(_DRIVER_CONFIGS) == names(DriverBase)
    assert {"cron", "on-demand"} == names(ScheduleBase)
    assert {"keep-all", "keep-last", "keep-for"} == names(RetentionPolicyBase)


def test_parse_config_builds_complete_backup():
    value = {
        "home": backup_config(
            ssh={
                "endpoint": "ssh://server",
                "identity_file": "/root/.ssh/server-key",
                "config_file": "/root/.ssh/config",
            },
            source={"btrfs": "/home", "remote": True},
            destination={"tar": "/backups", "remote": True},
            transforms=[
                {"zstd": 7, "remote": True},
                {"gpg": "/root/backup-key.asc", "remote": True},
            ],
        )
    }

    config = parse_config(value)
    backups = config.backups

    assert config.global_settings == {}
    assert tuple(backups) == ("home",)
    backup = backups["home"]
    ssh = SSHTarget(
        "ssh://server",
        Path("/root/.ssh/server-key"),
        Path("/root/.ssh/config"),
    )
    assert backup.name == "home"
    assert isinstance(backup.source, BtrfsDriver)
    assert backup.source.location == Path("/home")
    assert backup.source.ssh == ssh
    assert isinstance(backup.destination, TarDriver)
    assert backup.destination.location == Path("/backups")
    assert backup.destination.ssh is backup.source.ssh
    assert isinstance(backup.transforms[0], ZstdDriver)
    assert backup.transforms[0].level == 7
    assert backup.transforms[0].ssh is backup.source.ssh
    assert isinstance(backup.transforms[1], GPGDriver)
    assert backup.transforms[1].public_key == Path("/root/backup-key.asc")
    assert backup.transforms[1].ssh is backup.source.ssh
    assert backup.schedules == (
        Schedule("daily", CronSchedule("30 4 * * *")),
        Schedule("manual", OnDemandSchedule()),
    )
    assert backup.retention_policies == (KeepLast(7, "daily"), KeepAll("manual"))


def test_parse_config_builds_previous_name_lookup():
    config = parse_config(
        {
            "laptop-home": backup_config(
                previous_names=["home", "old-home"],
                schedules={
                    "nightly": {
                        "previous_names": ["daily", "old-daily"],
                        "cron": "30 4 * * *",
                        "retention": {"keep-last": 7},
                    }
                },
            )
        }
    )
    backup = config.backups["laptop-home"]

    assert backup.previous_names == ("home", "old-home")
    assert backup.schedules == (
        Schedule(
            "nightly",
            CronSchedule("30 4 * * *"),
            previous_names=("daily", "old-daily"),
        ),
        Schedule("manual", OnDemandSchedule()),
    )
    assert config.backups_by_name == {
        "laptop-home": backup,
        "home": backup,
        "old-home": backup,
    }


def test_parse_config_builds_backup_group():
    config = parse_config(
        {
            "local": {"group": ["root", "home"]},
            "root": backup_config(),
            "home": backup_config(),
        }
    )
    group = BackupGroup("local", ("root", "home"))
    all_group = config.groups[ALL_TARGET_NAME]

    assert all_group.name == ALL_TARGET_NAME
    assert all_group.members == ("root", "home")
    assert config.groups == {"local": group, ALL_TARGET_NAME: all_group}
    assert config.targets_by_name == {
        "root": config.backups["root"],
        "home": config.backups["home"],
        "local": group,
        ALL_TARGET_NAME: all_group,
    }
    assert config.backups_for_targets("local") == (
        config.backups["root"],
        config.backups["home"],
    )


def test_backup_groups_expand_recursively_in_order_without_duplicates():
    config = parse_config(
        {
            "local": {"group": ["root", "home"]},
            "everything": {"group": ["local", "remote", "root"]},
            "root": backup_config(),
            "home": backup_config(),
            "remote": backup_config(),
        }
    )

    assert config.backups_for_targets("home", "everything", "local") == (
        config.backups["home"],
        config.backups["root"],
        config.backups["remote"],
    )


def test_all_target_expands_every_backup_in_definition_order():
    config = parse_config(
        {
            "root": backup_config(),
            "home": backup_config(),
            "remote": backup_config(),
        }
    )

    assert config.groups[ALL_TARGET_NAME].members == ("root", "home", "remote")
    assert config.targets_by_name[ALL_TARGET_NAME] is config.groups[ALL_TARGET_NAME]
    assert config.backups_for_targets(ALL_TARGET_NAME) == tuple(config.backups.values())


def test_all_target_exists_when_config_has_no_backups():
    config = Config({}, {})

    assert config.groups[ALL_TARGET_NAME].members == ()
    assert config.backups_for_targets(ALL_TARGET_NAME) == ()


def test_backup_group_definition_order_does_not_matter():
    config = parse_config(
        {
            "everything": {"group": ["local", "remote"]},
            "local": {"group": ["home"]},
            "remote": backup_config(),
            "home": backup_config(),
        }
    )

    assert config.backups_for_targets("everything") == (
        config.backups["home"],
        config.backups["remote"],
    )


def test_backup_group_members_may_use_previous_backup_names():
    config = parse_config(
        {
            "local": {"group": ["old-home"]},
            "home": backup_config(previous_names=["old-home"]),
        }
    )

    assert config.backups_for_targets("local") == (config.backups["home"],)


def test_backups_for_targets_rejects_unknown_name():
    config = parse_config({"home": backup_config()})

    with pytest.raises(BackupTargetError, match="unknown backup target: 'missing'"):
        config.backups_for_targets("missing")


@pytest.mark.parametrize("members", [None, "home", {}, 1])
def test_parse_config_rejects_nonlist_backup_group(members):
    with pytest.raises(ConfigError, match="group 'local': group must be a list"):
        parse_config({"local": {"group": members}, "home": backup_config()})


def test_parse_config_rejects_empty_backup_group():
    with pytest.raises(ConfigError, match="group must contain at least one target"):
        parse_config({"local": {"group": []}, "home": backup_config()})


@pytest.mark.parametrize(
    ("members", "message"),
    [
        ([1], "group members must be strings"),
        (["bad/name"], "invalid group member name: 'bad/name'"),
    ],
)
def test_parse_config_rejects_invalid_backup_group_member(members, message):
    with pytest.raises(ConfigError, match=message):
        parse_config({"local": {"group": members}, "home": backup_config()})


def test_parse_config_rejects_unknown_backup_group_member():
    with pytest.raises(ConfigError, match="group 'local' references unknown target 'missing'"):
        parse_config({"local": {"group": ["missing"]}, "home": backup_config()})


def test_parse_config_rejects_backup_group_cycle():
    with pytest.raises(
        ConfigError,
        match="backup group cycle: everything -> local -> everything",
    ):
        parse_config(
            {
                "everything": {"group": ["local"]},
                "local": {"group": ["everything"]},
                "home": backup_config(),
            }
        )


def test_parse_config_rejects_invalid_backup_group_name():
    with pytest.raises(ConfigError, match="group '-local': invalid group name: '-local'"):
        parse_config({"-local": {"group": ["home"]}, "home": backup_config()})


def test_parse_config_rejects_unknown_backup_group_setting():
    with pytest.raises(ConfigError, match="group 'local': unknown settings: description"):
        parse_config(
            {
                "local": {"group": ["home"], "description": "Local backups"},
                "home": backup_config(),
            }
        )


def test_parse_config_rejects_group_name_that_is_a_previous_backup_name():
    with pytest.raises(
        ConfigError,
        match="target name 'old-home' is used by both backup 'home' and group 'old-home'",
    ):
        parse_config(
            {
                "old-home": {"group": ["home"]},
                "home": backup_config(previous_names=["old-home"]),
            }
        )


def test_parse_config_rejects_group_as_backup_source():
    with pytest.raises(
        ConfigError,
        match=(
            "backup 'replica' references group 'local' as its source; "
            "backup sources must reference a backup"
        ),
    ):
        parse_config(
            {
                "local": {"group": ["home"]},
                "home": backup_config(),
                "replica": backup_config(source={"backup": "local"}),
            }
        )


def test_parse_config_accepts_skip_unchanged():
    backup = parse_config({"home": backup_config(skip_unchanged=True)}).backups["home"]

    assert backup.skip_unchanged is True


@pytest.mark.parametrize("value", [None, 1, "yes", []])
def test_parse_config_rejects_nonboolean_skip_unchanged(value):
    with pytest.raises(ConfigError, match="skip_unchanged must be a boolean"):
        parse_config({"home": backup_config(skip_unchanged=value)})


@pytest.mark.parametrize("driver", ["rsync", "tar"])
def test_parse_config_rejects_skip_unchanged_for_unsupported_driver(driver):
    with pytest.raises(
        ConfigError,
        match=rf"destination driver {driver} does not support skip_unchanged",
    ):
        parse_config(
            {
                "home": backup_config(
                    destination={driver: "/destination"},
                    skip_unchanged=True,
                )
            }
        )


@pytest.mark.parametrize(
    "previous_names",
    ["home", None, {}, ["settings"], ["SETTINGS"], ["old/home"], [1]],
)
def test_parse_config_rejects_invalid_previous_backup_names(previous_names):
    with pytest.raises(ConfigError, match="previous_names|invalid previous backup name"):
        parse_config({"home": backup_config(previous_names=previous_names)})


@pytest.mark.parametrize("previous_names", [["home"], ["old", "old"]])
def test_parse_config_rejects_duplicate_backup_name_history(previous_names):
    with pytest.raises(ConfigError, match="duplicate backup name"):
        parse_config({"home": backup_config(previous_names=previous_names)})


@pytest.mark.parametrize(
    "config",
    [
        {
            "first": backup_config(previous_names=["second"]),
            "second": backup_config(),
        },
        {
            "first": backup_config(previous_names=["old"]),
            "second": backup_config(previous_names=["old"]),
        },
    ],
)
def test_parse_config_rejects_backup_name_history_collisions(config):
    with pytest.raises(ConfigError, match="backup name .* is used by both 'first' and 'second'"):
        parse_config(config)


@pytest.mark.parametrize(
    "previous_names",
    ["daily", None, {}, ["old/schedule"], [1]],
)
def test_parse_config_rejects_invalid_previous_schedule_names(previous_names):
    schedules = {
        "daily": {
            "previous_names": previous_names,
            "cron": "30 4 * * *",
            "retention": {"keep-last": 7},
        }
    }

    with pytest.raises(ConfigError, match="previous_names|invalid previous schedule name"):
        parse_config({"home": backup_config(schedules=schedules)})


@pytest.mark.parametrize("previous_names", [["daily"], ["old", "old"]])
def test_parse_config_rejects_duplicate_schedule_name_history(previous_names):
    schedules = {
        "daily": {
            "previous_names": previous_names,
            "cron": "30 4 * * *",
            "retention": {"keep-last": 7},
        }
    }

    with pytest.raises(ConfigError, match="duplicate schedule name"):
        parse_config({"home": backup_config(schedules=schedules)})


@pytest.mark.parametrize(
    "schedules",
    [
        {
            "nightly": {
                "previous_names": ["daily"],
                "cron": "0 1 * * *",
                "retention": {"keep-last": 7},
            },
            "daily": {
                "cron": "0 2 * * *",
                "retention": {"keep-last": 7},
            },
        },
        {
            "nightly": {
                "previous_names": ["old-daily"],
                "cron": "0 1 * * *",
                "retention": {"keep-last": 7},
            },
            "daily": {
                "previous_names": ["old-daily"],
                "cron": "0 2 * * *",
                "retention": {"keep-last": 7},
            },
        },
    ],
)
def test_parse_config_rejects_schedule_name_history_collisions(schedules):
    with pytest.raises(ConfigError, match="schedule name .* is used by both"):
        parse_config({"home": backup_config(schedules=schedules)})


@pytest.mark.parametrize(
    ("driver", "driver_type"),
    [
        ({"btrfs": {"location": "/source"}}, BtrfsDriver),
        ({"rsync": {"location": "/source"}}, RsyncDriver),
        ({"zfs": "tank/source"}, ZFSDriver),
    ],
)
def test_parse_config_constructs_source_and_destination_drivers(driver, driver_type):
    backup = parse_config({"home": backup_config(source=driver, destination=driver)}).backups[
        "home"
    ]

    assert isinstance(backup.source, driver_type)
    assert isinstance(backup.destination, driver_type)


def test_parse_config_builds_encrypted_tar_archive_pipeline():
    backup = parse_config(
        {
            "home": backup_config(
                source={"rsync": {"location": "/source"}},
                destination={
                    "tar": {
                        "location": "/archives",
                        "one_file_system": False,
                    }
                },
                transforms=[{"gpg": "/public-key.asc"}],
            )
        }
    ).backups["home"]

    assert isinstance(backup.source, RsyncDriver)
    assert isinstance(backup.destination, TarDriver)
    assert backup.destination.location == Path("/archives")
    assert not backup.destination.one_file_system
    assert tuple(
        (step.driver.name(), step.capability)
        for step in Pipeline(backup.source, backup.destination, backup.transforms).steps
    ) == (
        ("rsync", "source"),
        ("tar", "export"),
        ("gpg", "encrypt"),
        ("tar", "import"),
    )


def test_parse_config_accepts_forward_backup_source_reference():
    backups = parse_config(
        {
            "offsite": backup_config(
                source={"backup": "local"},
                destination={"btrfs": {"location": "/offsite"}},
            ),
            "local": backup_config(),
        }
    ).backups

    assert backups["offsite"].source == bckp.BackupSource("local")


def test_parse_config_rejects_incompatible_replication_without_artifacts():
    with pytest.raises(ConfigError) as error:
        parse_config(
            {
                "archive": backup_config(
                    source={"rsync": "/source"},
                    destination={"tar": "/archives"},
                ),
                "replica": backup_config(
                    source={"backup": "archive"},
                    destination={"btrfs": "/destination"},
                ),
            }
        )

    assert "backup 'replica': cannot build backup pipeline" in str(error.value)
    assert "produced: TarArchive" in str(error.value)


@pytest.mark.parametrize("value", [None, [], 1])
def test_parse_config_rejects_nonmapping(value):
    with pytest.raises(ConfigError, match="configuration must be a mapping"):
        parse_config(value)


def test_parse_config_rejects_empty_mapping():
    with pytest.raises(ConfigError, match="at least one backup is required"):
        parse_config({})


def test_parse_config_accepts_settings():
    value = {
        "settings": {
            "scheduler": {
                "max_concurrent_backups": 15,
                "timezone": "America/New_York",
            },
            "ssh": {
                "identity_file": "/root/.ssh/id_ed25519",
                "config_file": "/root/.ssh/config",
            },
        },
        "home": backup_config(
            destination={"tar": {"location": "/destination"}},
            transforms=[{"zstd": {}}],
        ),
    }

    config = parse_config(value)
    backup = config.backups["home"]
    assert isinstance(backup.source, BtrfsDriver)

    assert config.global_settings == {
        "scheduler": {
            "max_concurrent_backups": 15,
            "timezone": ZoneInfo("America/New_York"),
        },
        "ssh": {
            "identity_file": Path("/root/.ssh/id_ed25519"),
            "config_file": Path("/root/.ssh/config"),
        },
    }
    assert tuple(config.backups) == ("home",)
    assert backup.source.global_settings is config.global_settings
    assert backup.destination.global_settings is config.global_settings
    assert backup.transforms[0].global_settings is config.global_settings


def test_parse_config_applies_global_ssh_defaults():
    config = parse_config(
        {
            "settings": {
                "ssh": {
                    "identity_file": "/root/.ssh/id_ed25519",
                    "config_file": "/root/.ssh/config",
                }
            },
            "home": backup_config(
                ssh={"endpoint": "ssh://server"},
                source={"btrfs": "/source", "remote": True},
            ),
        }
    )

    source = config.backups["home"].source
    assert isinstance(source, BtrfsDriver)
    assert source.ssh == SSHTarget(
        "ssh://server",
        Path("/root/.ssh/id_ed25519"),
        Path("/root/.ssh/config"),
    )


def test_backup_ssh_settings_override_global_defaults():
    config = parse_config(
        {
            "settings": {
                "ssh": {
                    "identity_file": "/default-key",
                    "config_file": "/default-config",
                }
            },
            "home": backup_config(
                ssh={
                    "endpoint": "ssh://server",
                    "identity_file": "/backup-key",
                    "config_file": "/backup-config",
                },
                source={"btrfs": "/source", "remote": True},
            ),
        }
    )

    source = config.backups["home"].source
    assert isinstance(source, BtrfsDriver)
    assert source.ssh == SSHTarget(
        "ssh://server",
        Path("/backup-key"),
        Path("/backup-config"),
    )


@pytest.mark.parametrize(
    "defaults",
    [
        None,
        {"identity_file": "relative"},
        {"config_file": "relative"},
        {"unknown": "/value"},
    ],
)
def test_parse_config_rejects_invalid_global_ssh_defaults(defaults):
    with pytest.raises(ConfigError, match="invalid settings"):
        parse_config(
            {
                "settings": {"ssh": defaults},
                "home": backup_config(),
            }
        )


@pytest.mark.parametrize("value", [0, -1, True, "10"])
def test_parse_config_rejects_invalid_max_concurrent_backups(value):
    with pytest.raises(ConfigError, match="must be a positive integer"):
        parse_config(
            {
                "settings": {"scheduler": {"max_concurrent_backups": value}},
                "home": backup_config(),
            }
        )


@pytest.mark.parametrize("value", [None, 1, "Not/A_Timezone"])
def test_parse_config_rejects_invalid_timezone(value):
    with pytest.raises(ConfigError, match="timezone"):
        parse_config(
            {
                "settings": {"scheduler": {"timezone": value}},
                "home": backup_config(),
            }
        )


def test_parse_config_rejects_unknown_global_setting():
    with pytest.raises(ConfigError, match="extra keys not allowed"):
        parse_config({"settings": {"unknown": {}}, "home": backup_config()})


def test_parse_config_rejects_nonmapping_settings():
    with pytest.raises(ConfigError, match="settings must be a mapping"):
        parse_config({"settings": [], "home": backup_config()})


def test_parse_config_rejects_nonstring_setting_name():
    with pytest.raises(ConfigError, match="setting names must be strings"):
        parse_config({"settings": {1: "value"}, "home": backup_config()})


def test_parse_config_requires_backup_with_settings():
    with pytest.raises(ConfigError, match="at least one backup is required"):
        parse_config({"settings": {}})


def test_parse_config_collects_independent_errors():
    value = {
        "settings": [],
        "first": backup_config(
            source={"unknown": {}},
            unexpected=True,
        ),
        "second": backup_config(
            destination={"unknown": {}},
            transforms={},
        ),
    }

    with pytest.raises(ConfigError) as error:
        parse_config(value)

    assert error.value.messages == (
        "settings must be a mapping",
        "backup 'first': unknown settings: unexpected",
        "backup 'first': source uses unknown driver 'unknown'",
        "backup 'second': destination uses unknown driver 'unknown'",
        "backup 'second': transforms must be a list",
    )
    assert error.value.format() == (
        "configuration errors:\n"
        "  - settings must be a mapping\n"
        "  - backup 'first': unknown settings: unexpected\n"
        "  - backup 'first': source uses unknown driver 'unknown'\n"
        "  - backup 'second': destination uses unknown driver 'unknown'\n"
        "  - backup 'second': transforms must be a list"
    )


def _expand_config_targets(value, *target_names):
    backup_aliases = {
        alias: name
        for name, definition in value.items()
        if name != "settings" and "group" not in definition
        for alias in (name, *definition.get("previous_names", ()))
    }
    group_members = {
        name: definition["group"]
        for name, definition in value.items()
        if name != "settings" and "group" in definition
    }
    group_members[ALL_TARGET_NAME] = tuple(dict.fromkeys(backup_aliases.values()))
    expanded = {}

    def expand(name):
        if name in group_members:
            for member in group_members[name]:
                expand(member)
        else:
            canonical_name = backup_aliases[name]
            expanded.setdefault(canonical_name, None)

    for name in target_names:
        expand(name)
    return tuple(expanded)


@given(value=valid_configs())
def test_generated_valid_configs_parse(value):
    parsed = parse_config(value)
    backup_definitions = {
        name: definition
        for name, definition in value.items()
        if name != "settings" and "group" not in definition
    }
    group_definitions = {
        name: definition
        for name, definition in value.items()
        if name != "settings" and "group" in definition
    }

    assert set(parsed.backups) == set(backup_definitions)
    expected_groups = {
        name: BackupGroup(name, tuple(definition["group"]))
        for name, definition in group_definitions.items()
    }
    assert parsed.groups[ALL_TARGET_NAME].members == tuple(backup_definitions)
    assert {
        name: group for name, group in parsed.groups.items() if name != ALL_TARGET_NAME
    } == expected_groups
    expected_global_settings = value.get("settings", {}).copy()
    if scheduler := expected_global_settings.get("scheduler"):
        expected_global_settings["scheduler"] = {
            **scheduler,
            **(
                {} if "timezone" not in scheduler else {"timezone": ZoneInfo(scheduler["timezone"])}
            ),
        }
    if ssh := expected_global_settings.get("ssh"):
        expected_global_settings["ssh"] = {name: Path(path) for name, path in ssh.items()}
    assert parsed.global_settings == expected_global_settings
    for name, backup in parsed.backups.items():
        definition = backup_definitions[name]
        assert backup.previous_names == tuple(definition.get("previous_names", ()))
        assert backup.skip_unchanged is definition.get("skip_unchanged", False)

        configured_drivers = [(definition["destination"], backup.destination)]
        if isinstance(backup.source, bckp.BackupSource):
            assert backup.source.backup_name == definition["source"]["backup"]
        else:
            configured_drivers.append((definition["source"], backup.source))
        configured_drivers.extend(
            zip(definition.get("transforms", ()), backup.transforms, strict=True)
        )
        for driver_definition, driver in configured_drivers:
            driver_name = next(name for name in driver_definition if name != "remote")
            assert driver.name() == driver_name
            assert driver.global_settings is parsed.global_settings
            assert (driver.ssh is not None) is driver_definition.get("remote", False)

        configured_schedules = definition.get("schedules", {})
        has_on_demand = any("on-demand" in schedule for schedule in configured_schedules.values())
        expected_schedule_names = (
            *configured_schedules,
            *(("manual",) if not has_on_demand else ()),
        )
        assert tuple(schedule.name for schedule in backup.schedules) == expected_schedule_names
        for schedule in backup.schedules:
            expected_previous_names = configured_schedules.get(schedule.name, {}).get(
                "previous_names", ()
            )
            assert schedule.previous_names == tuple(expected_previous_names)

        expected_policy_count = sum(
            len(retention) if isinstance(retention, list) else 1
            for schedule in configured_schedules.values()
            for retention in (schedule["retention"],)
        ) + (not has_on_demand)
        assert len(backup.retention_policies) == expected_policy_count
        assert all(
            getattr(policy, "schedule_name", None) in expected_schedule_names
            for policy in backup.retention_policies
        )

    expected_target_names = (
        {
            alias
            for name, definition in backup_definitions.items()
            for alias in (name, *definition.get("previous_names", ()))
        }
        | set(group_definitions)
        | {ALL_TARGET_NAME}
    )
    assert set(parsed.targets_by_name) == expected_target_names
    for target_name in parsed.targets_by_name:
        assert tuple(backup.name for backup in parsed.backups_for_targets(target_name)) == (
            _expand_config_targets(value, target_name)
        )
    assert tuple(
        backup.name for backup in parsed.backups_for_targets(*parsed.targets_by_name)
    ) == _expand_config_targets(value, *parsed.targets_by_name)


@pytest.mark.parametrize("mutation", _INVALID_MUTATION_NAMES)
def test_invalid_config_generator_covers_every_mutation(mutation):
    definition, message = invalidate_backup(backup_config(), mutation)

    with pytest.raises(ConfigError) as error:
        parse_config({"home": definition})

    assert len(error.value.messages) == 1
    assert message in error.value.messages[0]


@given(case=invalid_configs())
def test_generated_invalid_configs_report_all_errors(case):
    value, messages = case

    with pytest.raises(ConfigError) as error:
        parse_config(value)

    assert len(error.value.messages) == len(messages)
    assert all(
        expected in actual for expected, actual in zip(messages, error.value.messages, strict=True)
    )
    assert str(error.value) == (
        "configuration errors:\n" + "\n".join(f"  - {message}" for message in error.value.messages)
    )


@pytest.mark.parametrize("mutation", _INVALID_GROUP_MUTATION_NAMES)
def test_invalid_group_generator_covers_every_mutation(mutation):
    value, message = invalid_group_config(("home", "local", "remote", "missing"), mutation)

    with pytest.raises(ConfigError) as error:
        parse_config(value)

    assert message in error.value.format()


@given(case=invalid_group_configs())
def test_generated_invalid_backup_groups_are_rejected(case):
    value, message = case

    with pytest.raises(ConfigError) as error:
        parse_config(value)

    assert message in error.value.format()


def test_parse_config_rejects_nonstring_backup_name():
    with pytest.raises(ConfigError, match="backup names must be strings"):
        parse_config({1: backup_config()})


@pytest.mark.parametrize("name", ["1home", "_home", "global_settings"])
def test_parse_config_accepts_numeric_or_underscore_backup_name(name):
    assert parse_config({name: backup_config()}).backups[name].name == name


@pytest.mark.parametrize("name", ["-home", "SETTINGS"])
def test_parse_config_rejects_invalid_backup_name(name):
    with pytest.raises(ConfigError, match=rf"backup '{name}': invalid backup name"):
        parse_config({name: backup_config()})


def test_parse_config_rejects_nonmapping_backup_settings():
    with pytest.raises(ConfigError, match="backup 'home': settings must be a mapping"):
        parse_config({"home": []})


@pytest.mark.parametrize("setting", ["source", "destination"])
def test_parse_config_rejects_missing_required_setting(setting):
    config = backup_config()
    del config[setting]

    with pytest.raises(ConfigError, match=f"missing required settings: {setting}"):
        parse_config({"home": config})


def test_parse_config_allows_omitted_schedules():
    config = backup_config()
    del config["schedules"]

    backup = parse_config({"home": config}).backups["home"]

    assert backup.schedules == (Schedule("manual", OnDemandSchedule()),)
    assert backup.retention_policies == (KeepAll("manual"),)


def test_parse_config_accepts_minimal_configuration():
    backup = parse_config(
        {
            "home": {
                "source": {"btrfs": "/home"},
                "destination": {"btrfs": "/backups"},
            }
        }
    ).backups["home"]

    assert isinstance(backup.source, BtrfsDriver)
    assert isinstance(backup.destination, BtrfsDriver)
    assert backup.source.location == Path("/home")
    assert backup.destination.location == Path("/backups")
    assert backup.schedules == (Schedule("manual", OnDemandSchedule()),)


@pytest.mark.parametrize("setting", ["unknown", 1])
def test_parse_config_rejects_unknown_setting(setting):
    config = backup_config()
    config[setting] = True

    with pytest.raises(ConfigError, match=f"unknown settings: {setting}"):
        parse_config({"home": config})


@pytest.mark.parametrize(
    ("setting", "value", "message"),
    [
        ("source", None, "source must select one driver"),
        (
            "source",
            {"btrfs": {}, "rsync": {}},
            "source must select one driver",
        ),
        ("source", {"unknown": {}}, "source uses unknown driver 'unknown'"),
        ("destination", [], "destination must select one driver"),
        ("transforms", {}, "transforms must be a list"),
        (
            "transforms",
            [{"zstd": {"level": 20}}],
            "transform 1 has invalid zstd configuration",
        ),
    ],
)
def test_parse_config_rejects_invalid_driver_selection(setting, value, message):
    with pytest.raises(ConfigError, match=message):
        parse_config({"home": backup_config(**{setting: value})})


def test_parse_config_rejects_invalid_driver_configuration():
    with pytest.raises(ConfigError, match="source has invalid btrfs configuration"):
        parse_config(
            {
                "home": backup_config(
                    source={"btrfs": {"location": "relative"}},
                )
            }
        )


@pytest.mark.parametrize(
    "ssh",
    [
        None,
        {},
        {"endpoint": "host", "identity_file": "/key"},
        {"endpoint": "ssh://host", "identity_file": 1},
        {"endpoint": "ssh://host", "identity_file": "relative"},
        {
            "endpoint": "ssh://host",
            "identity_file": "/key",
            "config_file": "relative",
        },
        {"endpoint": "ssh://host", "identity_file": "/key", "unknown": True},
    ],
)
def test_parse_config_rejects_invalid_ssh_target(ssh):
    with pytest.raises(ConfigError, match="invalid SSH configuration"):
        parse_config({"home": backup_config(ssh=ssh)})


def test_invalid_ssh_stops_backup_validation():
    definition = backup_config(
        ssh={},
        source={"btrfs": "/source", "remote": True},
        destination={"btrfs": "/destination", "remote": True},
        transforms=[{"zstd": {}, "remote": True}],
    )

    with pytest.raises(ConfigError) as error:
        parse_config({"home": definition})

    assert len(error.value.messages) == 1
    assert "invalid SSH configuration" in error.value.messages[0]
    assert "remote requires backup SSH configuration" not in error.value.format()


def test_invalid_backup_suppresses_dependent_group_errors():
    value = {
        "home": backup_config(ssh={}),
        "local": {"group": ["home"]},
    }

    with pytest.raises(ConfigError) as error:
        parse_config(value)

    assert "invalid SSH configuration" in error.value.format()
    assert "references unknown target" not in error.value.format()


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("source", {"btrfs": "/source", "remote": True}),
        ("destination", {"btrfs": "/destination", "remote": True}),
        ("transforms", [{"zstd": {}, "remote": True}]),
    ],
)
def test_parse_config_rejects_remote_driver_without_ssh(setting, value):
    with pytest.raises(ConfigError, match="remote requires backup SSH configuration"):
        parse_config({"home": backup_config(**{setting: value})})


@pytest.mark.parametrize("remote", [None, 1, "yes", []])
def test_parse_config_rejects_invalid_remote_setting(remote):
    source = {"btrfs": "/source", "remote": remote}

    with pytest.raises(ConfigError, match="remote must be a boolean"):
        parse_config({"home": backup_config(source=source)})


def test_parse_config_rejects_driver_ssh_configuration():
    source = {"btrfs": {"location": "/source", "ssh": {}}}

    with pytest.raises(ConfigError, match="extra keys not allowed.*ssh"):
        parse_config({"home": backup_config(source=source)})


def test_parse_config_rejects_target_setting():
    source = {
        "btrfs": {
            "location": "/source",
            "target": {
                "spec": "ssh://host",
                "key": "/key",
            },
        }
    }

    with pytest.raises(ConfigError, match="extra keys not allowed.*target"):
        parse_config({"home": backup_config(source=source)})


@pytest.mark.parametrize(
    "source",
    [
        {"backup": ""},
        {"backup": None},
        {"backup": 1},
        {"backup": "-local"},
        {"backup": "settings"},
        {"backup": "local.backup"},
    ],
)
def test_parse_config_rejects_invalid_backup_source_name(source):
    with pytest.raises(ConfigError, match="invalid source backup name"):
        parse_config({"home": backup_config(source=source)})


def test_parse_config_rejects_unknown_backup_source():
    with pytest.raises(ConfigError, match="references unknown source backup 'missing'"):
        parse_config({"offsite": backup_config(source={"backup": "missing"})})


def test_parse_config_collects_unknown_backup_sources():
    with pytest.raises(ConfigError) as error:
        parse_config(
            {
                "first": backup_config(source={"backup": "missing-first"}),
                "second": backup_config(source={"backup": "missing-second"}),
            }
        )

    assert error.value.messages == (
        "backup 'first' references unknown source backup 'missing-first'",
        "backup 'second' references unknown source backup 'missing-second'",
    )


def test_parse_config_rejects_backup_source_cycle():
    with pytest.raises(ConfigError, match="backup source cycle: first -> second -> first"):
        parse_config(
            {
                "first": backup_config(source={"backup": "second"}),
                "second": backup_config(source={"backup": "first"}),
            }
        )


def test_parse_config_collects_independent_backup_source_cycles():
    with pytest.raises(ConfigError) as error:
        parse_config(
            {
                "first": backup_config(source={"backup": "second"}),
                "second": backup_config(source={"backup": "first"}),
                "third": backup_config(source={"backup": "fourth"}),
                "fourth": backup_config(source={"backup": "third"}),
            }
        )

    assert error.value.messages == (
        "backup source cycle: first -> second -> first",
        "backup source cycle: third -> fourth -> third",
    )


def test_parse_config_rejects_self_as_backup_source():
    with pytest.raises(ConfigError, match="backup source cycle: home -> home"):
        parse_config({"home": backup_config(source={"backup": "home"})})


def test_parse_config_rejects_source_cycle_through_previous_name():
    with pytest.raises(ConfigError, match="backup source cycle: home -> home"):
        parse_config(
            {
                "home": backup_config(
                    source={"backup": "old-home"},
                    previous_names=["old-home"],
                )
            }
        )


def test_parse_config_accepts_previous_name_as_backup_source():
    config = parse_config(
        {
            "local": backup_config(previous_names=["old-local"]),
            "offsite": backup_config(source={"backup": "old-local"}),
        }
    )

    assert config.backups["offsite"].source == bckp.BackupSource("old-local")


def test_parse_config_rejects_replication_between_ssh_endpoints():
    def remote(endpoint):
        return {
            "ssh": {"endpoint": endpoint, "identity_file": "/key"},
            "destination": {"btrfs": "/destination", "remote": True},
        }

    with pytest.raises(ConfigError, match="use different SSH configurations"):
        parse_config(
            {
                "original": backup_config(**remote("ssh://first")),
                "replica": backup_config(
                    source={"backup": "original"},
                    **remote("ssh://second"),
                ),
            }
        )


def test_parse_config_accepts_replication_on_one_ssh_endpoint():
    ssh = {"endpoint": "ssh://server", "identity_file": "/key"}
    config = parse_config(
        {
            "original": backup_config(
                ssh=ssh,
                destination={"btrfs": "/original", "remote": True},
            ),
            "replica": backup_config(
                ssh=ssh,
                source={"backup": "original"},
                destination={"btrfs": "/replica", "remote": True},
            ),
        }
    )

    assert config.backups["original"].destination.ssh == config.backups["replica"].destination.ssh


def test_parse_config_rejects_requirements_setting():
    with pytest.raises(ConfigError, match="unknown settings: requirements"):
        parse_config({"home": backup_config(requirements=["encrypted"])})


def test_parse_config_rejects_drivers_setting():
    with pytest.raises(ConfigError, match="unknown settings: drivers"):
        parse_config({"home": backup_config(drivers=[])})


def test_parse_config_rejects_incompatible_pipeline():
    with pytest.raises(ConfigError, match="cannot build backup pipeline"):
        parse_config(
            {
                "home": backup_config(
                    source={"rsync": {"location": "/source"}},
                    destination={"zfs": "tank/backup"},
                )
            }
        )


def test_parse_config_rejects_driver_without_destination_capabilities():
    with pytest.raises(ConfigError, match="destination driver gpg does not provide: delete, list"):
        parse_config(
            {
                "offsite": backup_config(
                    source={"backup": "local"},
                    destination={"gpg": "/root/backup-key.asc"},
                ),
                "local": backup_config(),
            }
        )


def test_parse_config_rejects_destination_without_storage_capability():
    with pytest.raises(ConfigError, match="destination driver unstorable cannot store"):
        parse_config({"home": backup_config(destination={"unstorable": {}})})


def test_parse_config_rejects_driver_incompatible_with_destination():
    with pytest.raises(ConfigError, match="last usable route:.*gpg.encrypt"):
        parse_config({"home": backup_config(transforms=[{"gpg": "/key.asc"}])})


def test_parse_config_reads_yaml_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
home:
  source:
    btrfs:
      location: /source
  destination:
    btrfs:
      location: /destination
  schedules:
    daily:
      cron: "30 4 * * *"
      retention:
        keep-last: 7
""".lstrip()
    )

    config = parse_config(str(path))
    backups = config.backups

    assert config.global_settings == {}
    assert tuple(backups) == ("home",)
    assert isinstance(backups["home"].source, BtrfsDriver)


def test_parse_config_rejects_missing_file(tmp_path):
    path = tmp_path / "missing.yaml"

    with pytest.raises(ConfigError, match="could not read configuration file"):
        parse_config(path)


def test_parse_config_rejects_invalid_yaml(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("home: [")

    with pytest.raises(ConfigError, match="invalid YAML in configuration file"):
        parse_config(path)


def test_parse_schedules():
    schedules, retention = parse_schedules(
        {
            "hourly": {
                "cron": "0 * * * *",
                "retention": {"keep-last": 24},
            },
            "daily": {
                "cron": {"expression": "30 4 * * *"},
                "retention": [
                    {"keep-for": "30d"},
                    {"keep-last": {"count": 3}},
                ],
            },
            "manual": {
                "on-demand": {},
                "retention": {"keep-last": 2},
            },
        }
    )

    assert schedules == (
        Schedule("hourly", CronSchedule("0 * * * *")),
        Schedule("daily", CronSchedule("30 4 * * *")),
        Schedule("manual", OnDemandSchedule()),
    )
    assert retention == (
        KeepLast(24, "hourly"),
        KeepFor(timedelta(days=30), "daily"),
        KeepLast(3, "daily"),
        KeepLast(2, "manual"),
    )


def test_parse_schedules_accepts_numeric_leading_name():
    schedules, retention = parse_schedules(
        {
            "5minute": {
                "cron": "*/5 * * * *",
                "retention": {"keep-last": 288},
            }
        }
    )

    assert schedules[0] == Schedule("5minute", CronSchedule("*/5 * * * *"))
    assert retention[0] == KeepLast(288, "5minute")


def test_parse_schedules_adds_implicit_manual_schedule():
    schedules, retention = parse_schedules(
        {
            "daily": {
                "cron": "30 4 * * *",
                "retention": {"keep-last": 7},
            }
        }
    )

    assert schedules == (
        Schedule("daily", CronSchedule("30 4 * * *")),
        Schedule("manual", OnDemandSchedule()),
    )
    assert retention == (KeepLast(7, "daily"), KeepAll("manual"))


def test_parse_schedules_preserves_explicit_on_demand_schedule():
    schedules, retention = parse_schedules(
        {
            "adhoc": {
                "on-demand": {},
                "retention": {"keep-last": 4},
            }
        }
    )

    assert schedules == (Schedule("adhoc", OnDemandSchedule()),)
    assert retention == (KeepLast(4, "adhoc"),)


def test_parse_schedules_accepts_keep_all():
    schedules, retention = parse_schedules(
        {
            "manual": {
                "on-demand": {},
                "retention": {"keep-all": {}},
            }
        }
    )

    assert schedules == (Schedule("manual", OnDemandSchedule()),)
    assert retention == (KeepAll("manual"),)


@pytest.mark.parametrize("name", ["manual", "MANUAL", "Manual"])
def test_parse_schedules_reserves_manual_for_on_demand(name):
    with pytest.raises(ConfigError, match=rf"schedule '{name}' must be on-demand"):
        parse_schedules(
            {
                name: {
                    "cron": "30 4 * * *",
                    "retention": {"keep-last": 1},
                }
            }
        )


@pytest.mark.parametrize(
    "value",
    [None, [], "schedules", 1],
)
def test_parse_schedules_rejects_nonmapping(value):
    with pytest.raises(ConfigError, match="schedules must be a mapping"):
        parse_schedules(value)


def test_parse_schedules_accepts_empty_mapping():
    schedules, retention = parse_schedules({})

    assert schedules == (Schedule("manual", OnDemandSchedule()),)
    assert retention == (KeepAll("manual"),)


@pytest.mark.parametrize(
    "name",
    ["", None, 1, "../../../outside", "daily/../../outside", "daily,weekly", "daily backup"],
)
def test_parse_schedules_rejects_invalid_name(name):
    with pytest.raises(ConfigError, match="invalid schedule name"):
        parse_schedules({name: {}})


def test_parse_schedules_rejects_nonmapping_schedule():
    with pytest.raises(ConfigError, match="schedule 'hourly' must be a mapping"):
        parse_schedules({"hourly": "0 * * * *"})


@pytest.mark.parametrize(
    ("definition", "message"),
    [
        ({"cron": "0 * * * *"}, "schedule 'hourly' has no retention policy"),
        (
            {"cron": "0 * * * *", "retention": []},
            "schedule 'hourly' has no retention policy",
        ),
        (
            {"retention": {"keep-last": 24}},
            "schedule 'hourly' must select one schedule type",
        ),
        (
            {
                "cron": "0 * * * *",
                "other": {},
                "retention": {"keep-last": 24},
            },
            "schedule 'hourly' must select one schedule type",
        ),
        (
            {"unknown": {}, "retention": {"keep-last": 24}},
            "schedule 'hourly' uses unknown type 'unknown'",
        ),
        (
            {"cron": "invalid", "retention": {"keep-last": 24}},
            "schedule 'hourly' has invalid cron configuration",
        ),
        (
            {"cron": "0 * * * *", "retention": {}},
            "retention policies must select one policy type",
        ),
        (
            {
                "cron": "0 * * * *",
                "retention": {"keep-last": 24, "keep-for": "2d"},
            },
            "retention policies must select one policy type",
        ),
        (
            {"cron": "0 * * * *", "retention": {"unknown": 1}},
            "uses unknown retention policy 'unknown'",
        ),
        (
            {"cron": "0 * * * *", "retention": {"keep-last": 0}},
            "has invalid keep-last configuration",
        ),
    ],
)
def test_parse_schedules_rejects_invalid_definition(definition, message):
    with pytest.raises(ConfigError, match=message):
        parse_schedules({"hourly": definition})
