"""Tests for yaesm.config."""

from datetime import timedelta

import pytest

from yaesm.config import ConfigError, parse_schedules
from yaesm.retention import KeepFor, KeepLast
from yaesm.schedule import CronSchedule, Schedule


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
        }
    )

    assert schedules == (
        Schedule("hourly", CronSchedule("0 * * * *")),
        Schedule("daily", CronSchedule("30 4 * * *")),
    )
    assert retention == (
        KeepLast(24, "hourly"),
        KeepFor(timedelta(days=30), "daily"),
        KeepLast(3, "daily"),
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


@pytest.mark.parametrize("name", ["", None, 1])
def test_parse_schedules_rejects_invalid_name(name):
    with pytest.raises(ConfigError, match="schedule names must be nonempty strings"):
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
