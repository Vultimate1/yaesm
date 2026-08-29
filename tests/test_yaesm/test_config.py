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
from yaesm.driver.tardriver import TarDriver
from yaesm.driver.zfsdriver import ZFSDriver
from yaesm.driver.zstddriver import ZstdDriver
from yaesm.pipeline import Pipeline
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
    drivers = draw(_TRANSFORMS)
    backend = draw(st.sampled_from(("btrfs", "rsync") if drivers else ("btrfs", "rsync", "zfs")))
    if backend == "zfs":
        source = {"zfs": "source/data"}
        destination = {"zfs": "destination/backups"}
    else:
        source = {backend: {"location": "/source"}}
        destination = {"tar" if drivers else backend: {"location": "/destination"}}
    return backup_config(
        source=source,
        destination=destination,
        drivers=drivers,
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

    assert isinstance(backup.source, AutomaticallyDiscoveredDriver)
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
                "tar": {
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
        )
    }

    config = parse_config(value)
    backups = config.backups

    assert config.global_settings == {}
    assert tuple(backups) == ("home",)
    backup = backups["home"]
    assert backup.name == "home"
    assert isinstance(backup.source, BtrfsDriver)
    assert backup.source.location == Path("/home")
    assert backup.source.target == SSHTarget(
        "ssh://source",
        Path("/root/.ssh/source-key"),
    )
    assert isinstance(backup.destination, TarDriver)
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
    assert backup.schedules == (Schedule("daily", CronSchedule("30 4 * * *")),)
    assert backup.retention_policies == (KeepLast(7, "daily"),)


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
    )
    assert config.backups_by_name == {
        "laptop-home": backup,
        "home": backup,
        "old-home": backup,
    }


@pytest.mark.parametrize(
    "previous_names",
    ["home", None, {}, ["global_settings"], ["GLOBAL_SETTINGS"], ["old/home"], [1]],
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
                drivers=[{"gpg": "/public-key.asc"}],
            )
        }
    ).backups["home"]

    assert isinstance(backup.source, RsyncDriver)
    assert isinstance(backup.destination, TarDriver)
    assert backup.destination.location == Path("/archives")
    assert not backup.destination.one_file_system
    assert tuple(
        (step.driver.name(), step.capability)
        for step in Pipeline(backup.source, backup.destination, backup.drivers).steps
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
        "home": backup_config(
            destination={"tar": {"location": "/destination"}},
            drivers=[{"zstd": {}}],
        ),
    }

    config = parse_config(value)
    backup = config.backups["home"]
    assert isinstance(backup.source, BtrfsDriver)

    assert config.global_settings == value["global_settings"]
    assert tuple(config.backups) == ("home",)
    assert backup.source.global_settings is config.global_settings
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
            unexpected=True,
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
        "backup 'first': unknown settings: unexpected",
        "backup 'first': source uses unknown driver 'unknown'",
        "backup 'second': destination uses unknown driver 'unknown'",
        "backup 'second': drivers must be a list",
    )
    assert error.value.format() == (
        "configuration errors:\n"
        "  - global_settings must be a mapping\n"
        "  - backup 'first': unknown settings: unexpected\n"
        "  - backup 'first': source uses unknown driver 'unknown'\n"
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


def test_parse_config_rejects_requirements_setting():
    with pytest.raises(ConfigError, match="unknown settings: requirements"):
        parse_config({"home": backup_config(requirements=["encrypted"])})


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


def test_parse_config_rejects_driver_incompatible_with_destination():
    with pytest.raises(ConfigError, match="last usable route:.*gpg.encrypt"):
        parse_config({"home": backup_config(drivers=[{"gpg": "/key.asc"}])})


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
