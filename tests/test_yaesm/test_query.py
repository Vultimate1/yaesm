"""Tests for yaesm.query."""

from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

from yaesm.backup import Backup, BackupArtifact, BackupOperation
from yaesm.errors import YaesmError, YaesmValueError
from yaesm.query import ArtifactQuery, ArtifactQueryError
from yaesm.representation import Representation
from yaesm.schedule import CronSchedule, Schedule


def artifact(
    hour: int,
    minute: int = 0,
    schedule: str = "hourly",
    backup_name: str = "home",
) -> BackupArtifact:
    return BackupArtifact(
        BackupOperation(
            backup_name,
            schedule,
            datetime(2026, 8, 20, hour, minute),
        ),
        Representation(),
    )


def hours(artifacts) -> list[int]:
    return [item.operation.created_at.hour for item in artifacts]


def test_query_error_is_an_expected_value_error():
    assert all(
        issubclass(ArtifactQueryError, parent)
        for parent in (YaesmError, YaesmValueError, ValueError)
    )


@pytest.mark.parametrize(
    ("tokens", "query_type"),
    [
        ([], ArtifactQuery.Type.ALL),
        (["all"], ArtifactQuery.Type.ALL),
        (["newest"], ArtifactQuery.Type.NEWEST),
        (["oldest"], ArtifactQuery.Type.OLDEST),
    ],
)
def test_query_without_times(tokens, query_type):
    query = ArtifactQuery(tokens)

    assert query.type is query_type
    assert query.target is None
    assert query.start is None
    assert query.end is None


@pytest.mark.parametrize(
    ("tokens", "query_type", "expected"),
    [
        (
            ["after", "now-2h"],
            ArtifactQuery.Type.AFTER,
            datetime(2026, 8, 20, 10, 34, tzinfo=timezone.utc),
        ),
        (
            ["before", "now-30m"],
            ArtifactQuery.Type.BEFORE,
            datetime(2026, 8, 20, 12, 4, tzinfo=timezone.utc),
        ),
        (
            ["closest", "now-7d"],
            ArtifactQuery.Type.CLOSEST,
            datetime(2026, 8, 13, 12, 34, tzinfo=timezone.utc),
        ),
        (
            ["after", "2026-07-04T14:30"],
            ArtifactQuery.Type.AFTER,
            datetime(2026, 7, 4, 14, 30, tzinfo=timezone.utc),
        ),
        (
            ["before", "2026-07-04"],
            ArtifactQuery.Type.BEFORE,
            datetime(2026, 7, 4, tzinfo=timezone.utc),
        ),
        (
            ["closest", "08:15"],
            ArtifactQuery.Type.CLOSEST,
            datetime(2026, 8, 20, 8, 15, tzinfo=timezone.utc),
        ),
    ],
)
def test_query_with_target(tokens, query_type, expected):
    query = ArtifactQuery(tokens, now=datetime(2026, 8, 20, 12, 34, 56, 123456))

    assert query.type is query_type
    assert query.target == expected


def test_query_between_normalizes_endpoints():
    query = ArtifactQuery(
        ["between", "2026-08-20T10:00", "now-3d"],
        now=datetime(2026, 8, 20, 12),
    )

    assert query.type is ArtifactQuery.Type.BETWEEN
    assert query.start == datetime(2026, 8, 17, 12, tzinfo=timezone.utc)
    assert query.end == datetime(2026, 8, 20, 10, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "2026-11-01T01:30-04:00",
            datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc),
        ),
        (
            "2026-11-01T01:30-0500",
            datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc),
        ),
        ("2026-11-01T05:30Z", datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)),
    ],
)
def test_query_accepts_explicit_utc_offsets(value, expected):
    query = ArtifactQuery(["after", value], zone=ZoneInfo("America/New_York"))

    assert query.target == expected


def test_query_rejects_ambiguous_local_time():
    with pytest.raises(ArtifactQueryError, match="ambiguous; include a UTC offset"):
        ArtifactQuery(
            ["after", "2026-11-01T01:30"],
            zone=ZoneInfo("America/New_York"),
        )


def test_query_rejects_nonexistent_local_time():
    with pytest.raises(ArtifactQueryError, match="does not exist"):
        ArtifactQuery(
            ["after", "2026-03-08T02:30"],
            zone=ZoneInfo("America/New_York"),
        )


def test_query_supports_non_hour_timezone_offsets():
    query = ArtifactQuery(
        ["after", "2026-08-20T12:00"],
        zone=ZoneInfo("Asia/Kathmandu"),
    )

    assert query.target == datetime(2026, 8, 20, 6, 15, tzinfo=timezone.utc)


def test_query_relative_time_is_elapsed_time_across_dst():
    now = datetime(2026, 3, 9, 4, 30, tzinfo=timezone.utc)
    query = ArtifactQuery(["after", "now-1d"], now, ZoneInfo("America/New_York"))

    assert query.target == now - timedelta(days=1)


@pytest.mark.parametrize(
    "tokens",
    [
        ["unknown"],
        ["all", "now-1d"],
        ["after"],
        ["after", "now-1d", "now-2d"],
        ["between", "now-1d"],
        ["between", "now-1d", "now-2d", "now-3d"],
    ],
)
def test_query_rejects_invalid_grammar(tokens):
    with pytest.raises(ArtifactQueryError, match="invalid query"):
        ArtifactQuery(tokens)


@pytest.mark.parametrize(
    "value",
    [
        "now",
        "now-0m",
        "now-01h",
        "now-2w",
        "now-" + "9" * 1000 + "d",
        "2026-02-29",
        "2026-08-20T24:00",
        "2026-08-20T24:00Z",
        "2026-08-20T24:00+00:00",
        "2026-08-20T24:00-0500",
        "2026-08-20 12:00",
        "24:00",
        "8:15",
    ],
)
def test_query_rejects_invalid_times(value):
    with pytest.raises(ArtifactQueryError, match="invalid time"):
        ArtifactQuery(["after", value], now=datetime(2026, 8, 20, 12))


@pytest.mark.parametrize(
    ("tokens", "expected_hours"),
    [
        (["all"], [12, 11, 10, 9]),
        (["newest"], [12]),
        (["oldest"], [9]),
        (["after", "2026-08-20T10:00"], [12, 11]),
        (["before", "2026-08-20T11:00"], [10, 9]),
        (["between", "2026-08-20T10:00", "2026-08-20T11:00"], [11, 10]),
        (["between", "2026-08-20T11:00", "2026-08-20T10:00"], [11, 10]),
        (["between", "2026-08-20T10:00", "2026-08-20T10:00"], [10]),
        (["closest", "2026-08-20T10:30"], [11]),
        (["closest", "2026-08-20T10:00"], [10]),
        (["closest", "2026-08-20T13:00"], [12]),
        (["closest", "2026-08-20T08:00"], [9]),
    ],
)
def test_query_selects_artifacts(tokens, expected_hours):
    artifacts = [artifact(hour) for hour in (12, 11, 10, 9)]

    assert hours(ArtifactQuery(tokens).select(artifacts)) == expected_hours


@pytest.mark.parametrize(
    "tokens",
    [
        ["all"],
        ["newest"],
        ["oldest"],
        ["after", "2026-08-20T10:00"],
        ["before", "2026-08-20T10:00"],
        ["between", "2026-08-20T09:00", "2026-08-20T10:00"],
        ["closest", "2026-08-20T10:00"],
    ],
)
def test_query_selects_nothing_from_empty_list(tokens):
    assert ArtifactQuery(tokens).select([]) == []


@pytest.fixture
def backup():
    return Backup(
        "home",
        mock.Mock(),
        mock.Mock(),
        schedules=(Schedule("nightly", CronSchedule("0 1 * * *"), previous_names=("daily",)),),
    )


@pytest.mark.parametrize(
    ("tokens", "expected_hours"),
    [
        ((["all"],), [12, 11, 10]),
        ((["oldest"], ["newest"], ["newest"]), [12, 10]),
        ((["after", "2026-08-20T10:00"], ["newest"]), [12, 11]),
        ((["after", "2026-08-21"],), []),
        ((), []),
    ],
)
def test_select_artifacts_combines_queries_in_artifact_order(backup, tokens, expected_hours):
    artifacts = [artifact(hour) for hour in (12, 11, 10)]
    original = tuple(artifacts)
    queries = tuple(ArtifactQuery(query) for query in tokens)

    selected = ArtifactQuery.select_artifacts(backup, artifacts, queries)

    assert hours(selected) == expected_hours
    assert tuple(artifacts) == original


@pytest.mark.parametrize(
    ("schedules", "expected_hours"),
    [
        ((), [12, 11, 10]),
        (("nightly",), [11]),
        (("daily",), [11]),
        (("daily", "nightly"), [11]),
        (("daily", "weekly"), [11, 10]),
        (("weekly",), [10]),
        (("missing",), []),
    ],
)
def test_select_artifacts_filters_schedules_and_aliases(backup, schedules, expected_hours):
    artifacts = [artifact(12), artifact(11, schedule="nightly"), artifact(10, schedule="weekly")]
    original = tuple(artifacts)

    selected = ArtifactQuery.select_artifacts(backup, artifacts, (ArtifactQuery([]),), schedules)

    assert hours(selected) == expected_hours
    assert tuple(artifacts) == original


@pytest.mark.parametrize(
    ("tokens", "expected_hour"),
    [
        (["newest"], 11),
        (["oldest"], 9),
        (["closest", "2026-08-20T10:00"], 11),
    ],
)
def test_select_artifacts_filters_schedules_before_applying_queries(backup, tokens, expected_hour):
    artifacts = (
        artifact(12),
        artifact(11, schedule="nightly"),
        artifact(10),
        artifact(9, schedule="nightly"),
        artifact(8),
    )

    selected = ArtifactQuery.select_artifacts(
        backup, artifacts, (ArtifactQuery(tokens),), ("daily",)
    )

    assert hours(selected) == [expected_hour]


def test_select_artifacts_handles_empty_artifacts(backup):
    assert ArtifactQuery.select_artifacts(backup, (), (ArtifactQuery([]),)) == []
