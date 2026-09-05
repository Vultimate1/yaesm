"""Tests for yaesm.retention."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import voluptuous as vlp

from yaesm.backup import BackupArtifact, BackupOperation
from yaesm.errors import YaesmValueError
from yaesm.representation import Representation
from yaesm.retention import KeepAll, KeepFor, KeepLast, RetentionPolicyBase


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


def artifact_at(created_at: datetime) -> BackupArtifact:
    return BackupArtifact(BackupOperation("home", "hourly", created_at), Representation())


def test_keep_last():
    older = artifact("hourly", 1)
    newer = artifact("hourly", 3)
    newest = artifact("hourly", 5)

    assert KeepLast(2).retain([newer, older, newest], datetime(2026, 8, 6)) == [newest, newer]


def test_keep_last_sorts_repeated_dst_time_by_instant():
    zone = ZoneInfo("America/New_York")
    first = artifact_at(datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0))
    second = artifact_at(datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1))

    assert KeepLast(1).retain([first, second], datetime(2026, 11, 2)) == [second]


def test_keep_all():
    older = artifact("hourly", 1)
    newer = artifact("hourly", 3)

    assert KeepAll().retain([older, newer], datetime(2026, 8, 6)) == [newer, older]


def test_keep_all_sorts_repeated_dst_time_by_instant():
    zone = ZoneInfo("America/New_York")
    first = artifact_at(datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0))
    second = artifact_at(datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1))

    assert KeepAll().retain([first, second], datetime(2026, 11, 2)) == [second, first]


def test_keep_all_name():
    assert KeepAll.name() == "keep-all"


def test_keep_all_filters_by_schedule():
    hourly = artifact("hourly", 1)
    daily = artifact("daily", 2)

    assert KeepAll("hourly").retain([daily, hourly], datetime(2026, 8, 3)) == [hourly]


def test_keep_all_config_schema_constructs_policy():
    config = KeepAll.config_schema()({})

    assert config == {}
    assert KeepAll(**config) == KeepAll()


@pytest.mark.parametrize(
    "config",
    [None, [], True, 1, "all", {"unknown": True}, {"schedule_name": "daily"}],
)
def test_keep_all_config_schema_rejects_settings(config):
    with pytest.raises(vlp.Invalid):
        KeepAll.config_schema()(config)


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
    "value",
    [1, 2**63],
)
def test_keep_last_config_schema_constructs_policy(value):
    config = KeepLast.config_schema()(value)

    assert config == {"count": value}
    assert KeepLast(**config) == KeepLast(value)


@pytest.mark.parametrize(
    "count",
    [0, -1, True, False, 1.0, "2", None, [], {}],
)
def test_keep_last_config_schema_rejects_invalid_count(count):
    with pytest.raises(vlp.Invalid, match="count must be a positive integer"):
        KeepLast.config_schema()(count)


@pytest.mark.parametrize(
    "config",
    [
        None,
        [],
        "config",
        {},
        {"count": 2},
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


def test_keep_for_uses_elapsed_time_across_dst():
    zone = ZoneInfo("America/New_York")
    first = artifact_at(datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0))
    second = artifact_at(datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1))
    now = datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=1)

    assert KeepFor(timedelta(minutes=30)).retain([first, second], now) == [second]


def test_keep_for_accepts_utc_now_with_local_artifacts():
    artifact = artifact_at(
        datetime(2026, 8, 20, 12, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    )

    assert KeepFor(timedelta(minutes=1)).retain(
        [artifact],
        datetime(2026, 8, 20, 6, 31, tzinfo=timezone.utc),
    ) == [artifact]


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
    "value",
    [timedelta(microseconds=1)],
)
def test_keep_for_config_schema_constructs_policy(value):
    config = KeepFor.config_schema()(value)

    assert config == {"duration": value}
    assert KeepFor(**config) == KeepFor(value)


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
        KeepFor.config_schema()(duration)


@pytest.mark.parametrize(
    "config",
    [
        None,
        [],
        1,
        {},
        {"duration": timedelta(days=2)},
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
