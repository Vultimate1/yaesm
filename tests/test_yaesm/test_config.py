"""Tests for yaesm.config."""

from datetime import timedelta
from pathlib import Path

import pytest
import voluptuous as vlp
from hypothesis import given
from hypothesis import strategies as st

import yaesm.backup as bckp
import yaesm.config as config_module
from yaesm.config import ConfigError, parse_config, parse_schedules
from yaesm.driver.btrfsdriver import BtrfsDriver
from yaesm.driver.gpgdriver import GPGDriver
from yaesm.driver.rsyncdriver import RsyncDriver
from yaesm.driver.zfsdriver import ZFSDriver
from yaesm.driver.zstddriver import ZstdDriver
from yaesm.representation import DataProperty
from yaesm.retention import KeepFor, KeepLast
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


def backup_config(**settings):
    config = {
        "source": {"btrfs": {"location": "/source"}},
        "destination": {"btrfs": {"location": "/destination"}},
        "schedules": {
            "daily": {
                "cron": "30 4 * * *",
                "retention": {"keep-last": 7},
            }
        },
    }
    config.update(settings)
    return config


_BACKUP_NAMES = st.from_regex(r"[a-z][a-z0-9_-]{0,12}", fullmatch=True).filter(
    lambda name: name != "global_settings"
)
_SCHEDULE_NAMES = st.from_regex(r"[a-z][a-z0-9_-]{0,8}", fullmatch=True)
_RETENTION = st.one_of(
    st.integers(min_value=1, max_value=100).map(lambda count: {"keep-last": count}),
    st.tuples(
        st.integers(min_value=1, max_value=100),
        st.sampled_from(("s", "m", "h", "d", "w", "y")),
    ).map(lambda duration: {"keep-for": f"{duration[0]}{duration[1]}"}),
)
_SCHEDULES = st.dictionaries(
    _SCHEDULE_NAMES,
    st.builds(
        lambda implementation, retention: {
            **implementation,
            "retention": retention,
        },
        st.one_of(
            st.sampled_from(("* * * * *", "0 * * * *", "30 4 * * *")).map(
                lambda expression: {"cron": expression}
            ),
            st.just({"on-demand": {}}),
        ),
        _RETENTION,
    ),
    min_size=1,
    max_size=3,
)
_TRANSFORMS = st.lists(
    st.sampled_from(({"zstd": {}}, {"gpg": "/key.asc"})),
    max_size=2,
    unique_by=lambda driver: next(iter(driver)),
)


@st.composite
def valid_backup_definitions(draw):
    backend = draw(st.sampled_from(("btrfs", "rsync", "zfs")))
    if backend == "zfs":
        source = {"zfs": "source/data"}
        destination = {"zfs": "destination/backups"}
    else:
        source = {backend: {"location": "/source"}}
        destination = {backend: {"location": "/destination"}}
    return backup_config(
        source=source,
        destination=destination,
        drivers=draw(_TRANSFORMS),
        schedules=draw(_SCHEDULES),
    )


@st.composite
def valid_configs(draw):
    names = draw(st.lists(_BACKUP_NAMES, min_size=1, max_size=4, unique=True))
    config = {name: draw(valid_backup_definitions()) for name in names}
    if draw(st.booleans()):
        config["global_settings"] = draw(
            st.dictionaries(
                st.sampled_from(("timezone", "max_concurrent_backups", "notifications")),
                st.none() | st.booleans() | st.integers() | st.text(max_size=20),
                max_size=3,
            )
        )
    return config


_INVALID_MUTATIONS = st.sampled_from(
    (
        "missing-source",
        "unknown-source",
        "unknown-destination",
        "invalid-drivers",
        "invalid-requirements",
        "invalid-schedules",
        "unknown-setting",
    )
)


def invalidate_backup(config, mutation):
    match mutation:
        case "missing-source":
            del config["source"]
            return "missing required settings: source"
        case "unknown-source":
            config["source"] = {"unknown": {}}
            return "source uses unknown driver 'unknown'"
        case "unknown-destination":
            config["destination"] = {"unknown": {}}
            return "destination uses unknown driver 'unknown'"
        case "invalid-drivers":
            config["drivers"] = {}
            return "drivers must be a list"
        case "invalid-requirements":
            config["requirements"] = "snapshot"
            return "requirements must be a list"
        case "invalid-schedules":
            config["schedules"] = []
            return "schedules must be a mapping"
        case "unknown-setting":
            config["unexpected"] = True
            return "unknown settings: unexpected"
    raise AssertionError(f"unknown mutation: {mutation}")


@st.composite
def invalid_configs(draw):
    names = draw(st.lists(_BACKUP_NAMES, min_size=2, max_size=4, unique=True))
    config = {}
    messages = []
    if draw(st.booleans()):
        config["global_settings"] = []
        messages.append("global_settings must be a mapping")
    for name in names:
        definition = backup_config()
        message = invalidate_backup(definition, draw(_INVALID_MUTATIONS))
        config[name] = definition
        messages.append(f"backup {name!r}: {message}")
    return config, tuple(messages)


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

    assert isinstance(backup.source, bckp.DriverSource)
    assert isinstance(backup.source.driver, AutomaticallyDiscoveredDriver)
    assert isinstance(backup.destination, AutomaticallyDiscoveredDriver)
    assert isinstance(backup.schedules[0].implementation, AutomaticallyDiscoveredSchedule)
    assert backup.retention_policies == (AutomaticallyDiscoveredRetention(2, "automatic"),)


def test_config_has_no_hardcoded_type_registries():
    assert not hasattr(config_module, "_DRIVER_TYPES")
    assert not hasattr(config_module, "_SCHEDULE_TYPES")
    assert not hasattr(config_module, "_RETENTION_TYPES")


def test_parse_config_builds_complete_backup():
    value = {
        "home": backup_config(
            source={
                "btrfs": {
                    "location": "/home",
                    "target": {
                        "spec": "ssh://source",
                        "key": "/root/.ssh/source-key",
                    },
                }
            },
            destination={
                "btrfs": {
                    "location": "/backups",
                    "target": {
                        "spec": "ssh://backup",
                        "key": "/root/.ssh/backup-key",
                        "ssh_config": "/root/.ssh/config",
                    },
                }
            },
            drivers=[
                {"zstd": {"level": 7}},
                {"gpg": "/root/backup-key.asc"},
            ],
            requirements=["snapshot"],
        )
    }

    config = parse_config(value)
    backups = config.backups

    assert config.global_settings == {}
    assert tuple(backups) == ("home",)
    backup = backups["home"]
    assert backup.name == "home"
    assert isinstance(backup.source, bckp.DriverSource)
    assert isinstance(backup.source.driver, BtrfsDriver)
    assert backup.source.driver.location == Path("/home")
    assert backup.source.driver.target == SSHTarget(
        "ssh://source",
        Path("/root/.ssh/source-key"),
    )
    assert isinstance(backup.destination, BtrfsDriver)
    assert backup.destination.location == Path("/backups")
    assert backup.destination.target == SSHTarget(
        "ssh://backup",
        Path("/root/.ssh/backup-key"),
        Path("/root/.ssh/config"),
    )
    assert isinstance(backup.drivers[0], ZstdDriver)
    assert backup.drivers[0].level == 7
    assert isinstance(backup.drivers[1], GPGDriver)
    assert backup.drivers[1].public_key == Path("/root/backup-key.asc")
    assert backup.requirements == {DataProperty.SNAPSHOT}
    assert backup.schedules == (Schedule("daily", CronSchedule("30 4 * * *")),)
    assert backup.retention_policies == (KeepLast(7, "daily"),)


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

    assert isinstance(backup.source, bckp.DriverSource)
    assert isinstance(backup.source.driver, driver_type)
    assert isinstance(backup.destination, driver_type)


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


@pytest.mark.parametrize("value", [None, [], 1])
def test_parse_config_rejects_nonmapping(value):
    with pytest.raises(ConfigError, match="configuration must be a mapping"):
        parse_config(value)


def test_parse_config_rejects_empty_mapping():
    with pytest.raises(ConfigError, match="at least one backup is required"):
        parse_config({})


def test_parse_config_accepts_global_settings():
    value = {
        "global_settings": {
            "max_concurrent_backups": 15,
            "timezone": "America/New_York",
        },
        "home": backup_config(drivers=[{"zstd": {}}]),
    }

    config = parse_config(value)
    backup = config.backups["home"]
    assert isinstance(backup.source, bckp.DriverSource)

    assert config.global_settings == value["global_settings"]
    assert tuple(config.backups) == ("home",)
    assert backup.source.driver.global_settings is config.global_settings
    assert backup.destination.global_settings is config.global_settings
    assert backup.drivers[0].global_settings is config.global_settings


def test_parse_config_rejects_nonmapping_global_settings():
    with pytest.raises(ConfigError, match="global_settings must be a mapping"):
        parse_config({"global_settings": [], "home": backup_config()})


def test_parse_config_rejects_nonstring_global_setting_name():
    with pytest.raises(ConfigError, match="global setting names must be strings"):
        parse_config({"global_settings": {1: "value"}, "home": backup_config()})


def test_parse_config_requires_backup_with_global_settings():
    with pytest.raises(ConfigError, match="at least one backup is required"):
        parse_config({"global_settings": {}})


def test_parse_config_collects_independent_errors():
    value = {
        "global_settings": [],
        "first": backup_config(
            source={"unknown": {}},
            requirements="snapshot",
        ),
        "second": backup_config(
            destination={"unknown": {}},
            drivers={},
        ),
    }

    with pytest.raises(ConfigError) as error:
        parse_config(value)

    assert error.value.messages == (
        "global_settings must be a mapping",
        "backup 'first': source uses unknown driver 'unknown'",
        "backup 'first': requirements must be a list",
        "backup 'second': destination uses unknown driver 'unknown'",
        "backup 'second': drivers must be a list",
    )
    assert error.value.format() == (
        "configuration errors:\n"
        "  - global_settings must be a mapping\n"
        "  - backup 'first': source uses unknown driver 'unknown'\n"
        "  - backup 'first': requirements must be a list\n"
        "  - backup 'second': destination uses unknown driver 'unknown'\n"
        "  - backup 'second': drivers must be a list"
    )


@given(value=valid_configs())
def test_generated_valid_configs_parse(value):
    parsed = parse_config(value)

    assert set(parsed.backups) == set(value) - {"global_settings"}


@given(case=invalid_configs())
def test_generated_invalid_configs_report_all_errors(case):
    value, messages = case

    with pytest.raises(ConfigError) as error:
        parse_config(value)

    assert error.value.messages == messages
    assert str(error.value) == (
        "configuration errors:\n" + "\n".join(f"  - {message}" for message in messages)
    )


def test_parse_config_rejects_nonstring_backup_name():
    with pytest.raises(ConfigError, match="backup names must be strings"):
        parse_config({1: backup_config()})


def test_parse_config_rejects_invalid_backup_name():
    with pytest.raises(ConfigError, match="backup '1home': invalid backup name"):
        parse_config({"1home": backup_config()})


def test_parse_config_rejects_nonmapping_backup_settings():
    with pytest.raises(ConfigError, match="backup 'home': settings must be a mapping"):
        parse_config({"home": []})


@pytest.mark.parametrize("setting", ["source", "destination", "schedules"])
def test_parse_config_rejects_missing_required_setting(setting):
    config = backup_config()
    del config[setting]

    with pytest.raises(ConfigError, match=f"missing required settings: {setting}"):
        parse_config({"home": config})


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
        ("drivers", {}, "drivers must be a list"),
        (
            "drivers",
            [{"zstd": {"level": 20}}],
            "driver 1 has invalid zstd configuration",
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
    "target",
    [
        None,
        {},
        {"spec": "host", "key": "/key"},
        {"spec": "ssh://host", "key": 1},
        {"spec": "ssh://host", "key": "relative"},
        {"spec": "ssh://host", "key": "/key", "ssh_config": "relative"},
        {"spec": "ssh://host", "key": "/key", "unknown": True},
    ],
)
def test_parse_config_rejects_invalid_ssh_target(target):
    source = {"btrfs": {"location": "/source", "target": target}}

    with pytest.raises(ConfigError, match="invalid SSH target"):
        parse_config({"home": backup_config(source=source)})


@pytest.mark.parametrize("source", [{"backup": ""}, {"backup": None}, {"backup": 1}])
def test_parse_config_rejects_invalid_backup_source_name(source):
    with pytest.raises(ConfigError, match="source backup name must be a nonempty string"):
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


def test_parse_config_rejects_nonlist_requirements():
    with pytest.raises(ConfigError, match="requirements must be a list"):
        parse_config({"home": backup_config(requirements="snapshot")})


@pytest.mark.parametrize("requirement", ["unknown", None, 1])
def test_parse_config_rejects_unknown_requirement(requirement):
    with pytest.raises(ConfigError, match="unknown data requirement"):
        parse_config({"home": backup_config(requirements=[requirement])})


def test_parse_config_removes_duplicate_requirements():
    backup = parse_config({"home": backup_config(requirements=["snapshot", "snapshot"])}).backups[
        "home"
    ]

    assert backup.requirements == {DataProperty.SNAPSHOT}


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


def test_parse_config_rejects_unsatisfied_requirement():
    with pytest.raises(ConfigError, match="missing required properties: encrypted"):
        parse_config({"home": backup_config(requirements=["encrypted"])})


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
    assert isinstance(backups["home"].source, bckp.DriverSource)


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


@pytest.mark.parametrize(
    "value",
    [None, [], "schedules", 1],
)
def test_parse_schedules_rejects_nonmapping(value):
    with pytest.raises(ConfigError, match="schedules must be a mapping"):
        parse_schedules(value)


def test_parse_schedules_rejects_empty_mapping():
    with pytest.raises(ConfigError, match="at least one schedule is required"):
        parse_schedules({})


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
