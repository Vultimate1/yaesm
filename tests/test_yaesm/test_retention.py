"""Tests for yaesm.retention."""

from datetime import datetime, timedelta

import pytest
import voluptuous as vlp

from yaesm.backup import BackupArtifact, BackupOperation
from yaesm.errors import YaesmValueError
from yaesm.representation import Representation
from yaesm.retention import KeepFor, KeepLast, RetentionPolicyBase


class PolicyWithoutConfiguration(RetentionPolicyBase):
    @classmethod
    def name(cls):
        return "missing-configuration"

    def retain(self, artifacts, now):
        return []


class PolicyWithoutName(RetentionPolicyBase):
    @staticmethod
    def config_schema():
        return vlp.Schema({})

    def retain(self, artifacts, now):
        return []


def artifact(schedule_name: str, day: int) -> BackupArtifact:
    operation = BackupOperation("home", schedule_name, datetime(2026, 8, day))
    return BackupArtifact(operation, Representation())


def test_keep_last():
    older = artifact("hourly", 1)
    newer = artifact("hourly", 3)
    newest = artifact("hourly", 5)

    assert KeepLast(2).retain([newer, older, newest], datetime(2026, 8, 6)) == [newest, newer]


def test_keep_last_name():
    assert KeepLast.name() == "keep-last"


def test_keep_last_filters_by_schedule():
    hourly = artifact("hourly", 1)
    daily = artifact("daily", 2)

    assert KeepLast(1, "hourly").retain([daily, hourly], datetime(2026, 8, 3)) == [hourly]


def test_keep_last_rejects_invalid_count():
    with pytest.raises(YaesmValueError, match="count must be at least 1"):
        KeepLast(0)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"count": 1}, KeepLast(1)),
        ({"count": 2**63}, KeepLast(2**63)),
    ],
)
def test_keep_last_config_schema_constructs_policy(value, expected):
    config = KeepLast.config_schema()(value)

    assert config == value
    assert KeepLast(**config) == expected


def test_keep_last_config_schema_accepts_shorthand():
    assert KeepLast.config_schema()(2) == {"count": 2}


@pytest.mark.parametrize(
    "count",
    [0, -1, True, False, 1.0, "2", None, [], {}],
)
def test_keep_last_config_schema_rejects_invalid_count(count):
    with pytest.raises(vlp.Invalid, match="count must be a positive integer"):
        KeepLast.config_schema()({"count": count})


@pytest.mark.parametrize(
    "config",
    [
        None,
        [],
        "config",
        {},
        {"count": 2, "unknown": True},
        {"count": 2, "schedule_name": "hourly"},
    ],
)
def test_keep_last_config_schema_rejects_invalid_structure(config):
    with pytest.raises(vlp.Invalid):
        KeepLast.config_schema()(config)


def test_keep_for():
    expired = artifact("hourly", 1)
    boundary = artifact("hourly", 2)
    newest = artifact("hourly", 5)

    assert KeepFor(timedelta(days=4)).retain(
        [boundary, newest, expired],
        datetime(2026, 8, 6),
    ) == [newest, boundary]


def test_keep_for_name():
    assert KeepFor.name() == "keep-for"


def test_keep_for_filters_by_schedule():
    hourly = artifact("hourly", 5)
    daily = artifact("daily", 5)

    assert KeepFor(timedelta(days=2), "daily").retain(
        [hourly, daily],
        datetime(2026, 8, 6),
    ) == [daily]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"duration": timedelta(microseconds=1)}, KeepFor(timedelta(microseconds=1))),
    ],
)
def test_keep_for_config_schema_constructs_policy(value, expected):
    config = KeepFor.config_schema()(value)

    assert config == value
    assert KeepFor(**config) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (timedelta(hours=2), timedelta(hours=2)),
        ("2h", timedelta(hours=2)),
        ("2d", timedelta(days=2)),
        ("2w", timedelta(weeks=2)),
        ("2y", timedelta(days=730)),
    ],
)
def test_keep_for_config_schema_accepts_shorthand(value, expected):
    assert KeepFor.config_schema()(value) == {"duration": expected}


@pytest.mark.parametrize(
    "duration",
    [
        timedelta(),
        timedelta(microseconds=-1),
        None,
        True,
        1,
        1.0,
        "0d",
        "-1d",
        "2 days",
        "1x",
        f"{'9' * 100}d",
        [],
        {},
    ],
)
def test_keep_for_config_schema_rejects_invalid_duration(duration):
    with pytest.raises(vlp.Invalid, match="duration must be a positive duration"):
        KeepFor.config_schema()({"duration": duration})


@pytest.mark.parametrize(
    "config",
    [
        None,
        [],
        1,
        {},
        {"duration": timedelta(days=2), "unknown": True},
        {"duration": timedelta(days=2), "schedule_name": "daily"},
    ],
)
def test_keep_for_config_schema_rejects_invalid_structure(config):
    with pytest.raises(vlp.Invalid):
        KeepFor.config_schema()(config)


@pytest.mark.parametrize("duration", [timedelta(), timedelta(seconds=-1)])
def test_keep_for_rejects_nonpositive_duration(duration):
    with pytest.raises(YaesmValueError, match="duration must be greater than zero"):
        KeepFor(duration)


def test_retention_policy_configuration_schema_is_required():
    with pytest.raises(TypeError):
        PolicyWithoutConfiguration()


def test_retention_policy_name_is_required():
    with pytest.raises(TypeError):
        PolicyWithoutName()
