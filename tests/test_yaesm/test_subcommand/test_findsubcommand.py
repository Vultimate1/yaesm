"""Tests for yaesm.subcommand.findsubcommand."""

import argparse
from datetime import datetime
from unittest import mock

import pytest

from yaesm.backup import Backup, BackupArtifact, BackupOperation
from yaesm.config import Config
from yaesm.errors import YaesmError, YaesmValueError
from yaesm.representation import Representation
from yaesm.subcommand.findsubcommand import (
    FindError,
    FindQuery,
    FindQueryError,
    FindSubcommand,
)


def arguments(*values: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    FindSubcommand.add_argparser_arguments(parser)
    return parser.parse_args(values)


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


def configured_backup(name: str, artifacts: tuple[BackupArtifact, ...]) -> tuple[Backup, mock.Mock]:
    destination = mock.Mock()
    destination.cap_list.return_value = artifacts
    destination.format_locator.side_effect = lambda item: f"locator:{item.name}"
    backup = Backup(name, mock.Mock(), destination)
    return backup, destination


def hours(artifacts) -> list[int]:
    return [item.operation.created_at.hour for item in artifacts]


def test_find_errors_are_expected_value_errors():
    assert issubclass(FindError, (YaesmError, YaesmValueError, ValueError))
    assert issubclass(FindQueryError, (FindError, YaesmError, ValueError))


def test_find_arguments_with_positional_query():
    parsed = arguments("home", "after", "2026-01-01")

    assert parsed.backup_names == ("home",)
    assert parsed.query == ["after", "2026-01-01"]
    assert parsed.additional_queries == []


def test_find_arguments_with_additional_queries():
    parsed = arguments(
        "home",
        "newest",
        "--query",
        "oldest",
        "--query",
        "closest",
        "12:30",
    )

    assert parsed.query == ["newest"]
    assert parsed.additional_queries == [["oldest"], ["closest", "12:30"]]


def test_find_arguments_with_only_optional_queries():
    parsed = arguments("home", "-q", "oldest", "-q", "closest", "12:30")

    assert parsed.query == []
    assert parsed.additional_queries == [["oldest"], ["closest", "12:30"]]


def test_find_argument_defaults():
    parsed = arguments("home")

    assert parsed.query == []
    assert parsed.additional_queries == []
    assert parsed.schedules == []


def test_find_arguments_normalize_names_and_schedules():
    parsed = arguments(
        "home,,root, home, ,root",
        "--schedules",
        "hourly,,daily,hourly",
        "--schedule",
        "weekly",
    )

    assert parsed.backup_names == ("home", "root")
    assert parsed.schedules == ["hourly", "daily", "weekly"]


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
        (["after", "now-2h"], FindQuery.Type.AFTER, datetime(2026, 8, 20, 10, 34)),
        (["before", "now-30m"], FindQuery.Type.BEFORE, datetime(2026, 8, 20, 12, 4)),
        (["closest", "now-7d"], FindQuery.Type.CLOSEST, datetime(2026, 8, 13, 12, 34)),
        (
            ["after", "2026-07-04T14:30"],
            FindQuery.Type.AFTER,
            datetime(2026, 7, 4, 14, 30),
        ),
        (["before", "2026-07-04"], FindQuery.Type.BEFORE, datetime(2026, 7, 4)),
        (["closest", "08:15"], FindQuery.Type.CLOSEST, datetime(2026, 8, 20, 8, 15)),
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
    assert query.start == datetime(2026, 8, 17, 12)
    assert query.end == datetime(2026, 8, 20, 10)


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


def test_find_defaults_to_all_and_formats_locators(capsys):
    artifacts = (artifact(12), artifact(10, schedule="daily"))
    backup, destination = configured_backup("home", artifacts)

    assert FindSubcommand().main(Config({}, {"home": backup}), arguments("home")) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"locator:{artifacts[0].name}",
        f"locator:{artifacts[1].name}",
    ]
    destination.cap_list.assert_called_once_with("home")
    assert destination.format_locator.call_args_list == [
        mock.call(artifacts[0]),
        mock.call(artifacts[1]),
    ]


def test_find_combines_queries_without_duplicates(capsys):
    artifacts = (artifact(12), artifact(11), artifact(10))
    backup, _destination = configured_backup("home", artifacts)
    parsed = arguments(
        "home",
        "newest",
        "--query",
        "after",
        "2026-08-20T10:00",
    )

    assert FindSubcommand().main(Config({}, {"home": backup}), parsed) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"locator:{artifacts[0].name}",
        f"locator:{artifacts[1].name}",
    ]


def test_optional_queries_do_not_implicitly_add_all(capsys):
    artifacts = (artifact(12), artifact(11), artifact(10))
    backup, _destination = configured_backup("home", artifacts)
    parsed = arguments(
        "home",
        "--query",
        "oldest",
        "--query",
        "closest",
        "2026-08-20T11:00",
    )

    assert FindSubcommand().main(Config({}, {"home": backup}), parsed) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"locator:{artifacts[1].name}",
        f"locator:{artifacts[2].name}",
    ]


def test_find_supports_multiple_backup_names_in_requested_order(capsys):
    home_artifact = artifact(12, backup_name="home")
    root_artifact = artifact(11, backup_name="root")
    home, _ = configured_backup("home", (home_artifact,))
    root, _ = configured_backup("root", (root_artifact,))
    config = Config({}, {"root": root, "home": home})

    assert FindSubcommand().main(config, arguments("home,root")) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"locator:{home_artifact.name}",
        f"locator:{root_artifact.name}",
    ]


def test_find_filters_schedules(capsys):
    hourly = artifact(12)
    daily = artifact(11, schedule="daily")
    weekly = artifact(10, schedule="weekly")
    backup, _destination = configured_backup("home", (hourly, daily, weekly))

    assert (
        FindSubcommand().main(
            Config({}, {"home": backup}),
            arguments("home", "--schedules", "daily,weekly"),
        )
        == 0
    )

    assert capsys.readouterr().out.splitlines() == [
        f"locator:{daily.name}",
        f"locator:{weekly.name}",
    ]


def test_find_returns_success_without_matches(capsys):
    backup, _destination = configured_backup("home", (artifact(12),))

    assert (
        FindSubcommand().main(
            Config({}, {"home": backup}),
            arguments("home", "after", "2026-08-21"),
        )
        == 0
    )
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("value", "error"),
    [(",", "no backup names specified"), ("missing", "unknown backup: 'missing'")],
)
def test_find_rejects_invalid_backup_selection(value, error):
    with pytest.raises(FindError, match=error):
        FindSubcommand().main(Config({}, {}), arguments(value))


def test_find_rejects_invalid_query_before_listing():
    backup, destination = configured_backup("home", ())

    with pytest.raises(FindQueryError, match="invalid query"):
        FindSubcommand().main(
            Config({}, {"home": backup}),
            arguments("home", "after"),
        )

    destination.cap_list.assert_not_called()
