"""Tests for yaesm.query."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from yaesm.backup import BackupArtifact, BackupOperation
from yaesm.errors import YaesmError, YaesmValueError
from yaesm.query import FindQuery, FindQueryError
from yaesm.representation import Representation


def artifact(hour: int) -> BackupArtifact:
    return BackupArtifact(
        BackupOperation("home", "hourly", datetime(2026, 8, 20, hour)),
        Representation(),
    )


def hours(artifacts) -> list[int]:
    return [item.operation.created_at.hour for item in artifacts]


def test_find_query_error_is_expected_value_error():
    assert all(
        issubclass(FindQueryError, parent) for parent in (YaesmError, YaesmValueError, ValueError)
    )


@pytest.mark.parametrize(
    ("tokens", "query_type"),
    [
        ([], FindQuery.Type.ALL),
        (["all"], FindQuery.Type.ALL),
        (["newest"], FindQuery.Type.NEWEST),
        (["oldest"], FindQuery.Type.OLDEST),
    ],
)
def test_find_query_without_times(tokens, query_type):
    query = FindQuery(tokens)

    assert query.type is query_type
    assert query.target is None
    assert query.start is None
    assert query.end is None


@pytest.mark.parametrize(
    ("tokens", "query_type", "expected"),
    [
        (
            ["after", "now-2h"],
            FindQuery.Type.AFTER,
            datetime(2026, 8, 20, 10, 34, tzinfo=timezone.utc),
        ),
        (
            ["before", "now-30m"],
            FindQuery.Type.BEFORE,
            datetime(2026, 8, 20, 12, 4, tzinfo=timezone.utc),
        ),
        (
            ["closest", "now-7d"],
            FindQuery.Type.CLOSEST,
            datetime(2026, 8, 13, 12, 34, tzinfo=timezone.utc),
        ),
        (
            ["after", "2026-07-04T14:30"],
            FindQuery.Type.AFTER,
            datetime(2026, 7, 4, 14, 30, tzinfo=timezone.utc),
        ),
        (
            ["before", "2026-07-04"],
            FindQuery.Type.BEFORE,
            datetime(2026, 7, 4, tzinfo=timezone.utc),
        ),
        (
            ["closest", "08:15"],
            FindQuery.Type.CLOSEST,
            datetime(2026, 8, 20, 8, 15, tzinfo=timezone.utc),
        ),
    ],
)
def test_find_query_with_target(tokens, query_type, expected):
    query = FindQuery(tokens, now=datetime(2026, 8, 20, 12, 34, 56, 123456))

    assert query.type is query_type
    assert query.target == expected


def test_find_query_between_normalizes_endpoints():
    query = FindQuery(
        ["between", "2026-08-20T10:00", "now-3d"],
        now=datetime(2026, 8, 20, 12),
    )

    assert query.type is FindQuery.Type.BETWEEN
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
def test_find_query_accepts_explicit_utc_offsets(value, expected):
    query = FindQuery(["after", value], zone=ZoneInfo("America/New_York"))

    assert query.target == expected


def test_find_query_rejects_ambiguous_local_time():
    with pytest.raises(FindQueryError, match="ambiguous; include a UTC offset"):
        FindQuery(
            ["after", "2026-11-01T01:30"],
            zone=ZoneInfo("America/New_York"),
        )


def test_find_query_rejects_nonexistent_local_time():
    with pytest.raises(FindQueryError, match="does not exist"):
        FindQuery(
            ["after", "2026-03-08T02:30"],
            zone=ZoneInfo("America/New_York"),
        )


def test_find_query_supports_non_hour_timezone_offsets():
    query = FindQuery(
        ["after", "2026-08-20T12:00"],
        zone=ZoneInfo("Asia/Kathmandu"),
    )

    assert query.target == datetime(2026, 8, 20, 6, 15, tzinfo=timezone.utc)


def test_find_query_relative_time_is_elapsed_time_across_dst():
    now = datetime(2026, 3, 9, 4, 30, tzinfo=timezone.utc)
    query = FindQuery(["after", "now-1d"], now, ZoneInfo("America/New_York"))

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
def test_find_query_rejects_invalid_grammar(tokens):
    with pytest.raises(FindQueryError, match="invalid query"):
        FindQuery(tokens)


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
        "2026-08-20 12:00",
        "8:15",
    ],
)
def test_find_query_rejects_invalid_times(value):
    with pytest.raises(FindQueryError, match="invalid time"):
        FindQuery(["after", value], now=datetime(2026, 8, 20, 12))


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
def test_find_query_selects_artifacts(tokens, expected_hours):
    artifacts = [artifact(hour) for hour in (12, 11, 10, 9)]

    assert hours(FindQuery(tokens).select(artifacts)) == expected_hours


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
def test_find_query_selects_nothing_from_empty_list(tokens):
    assert FindQuery(tokens).select([]) == []
