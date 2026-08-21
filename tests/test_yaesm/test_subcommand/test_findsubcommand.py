import argparse
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import yaesm.backup as bckp
from yaesm.backup import Backup
from yaesm.sshtarget import SSHTarget
from yaesm.subcommand.findsubcommand import FindQuery, FindQueryError, FindSubcommand
from yaesm.timeframe import DailyTimeframe, HourlyTimeframe


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    FindSubcommand.add_argparser_arguments(parser)
    return parser.parse_args(argv)


def _snapshot(hour: int, minute: int = 0, timeframe: str = "hourly") -> Path:
    return Path(f"/backups/yaesm-foo-{timeframe}.2026_08_20_{hour:02}:{minute:02}")


def _make_backup(tmp_path: Path, name: str) -> Backup:
    src_dir = tmp_path / f"{name}-src"
    dst_dir = tmp_path / f"{name}-dst"
    src_dir.mkdir()
    dst_dir.mkdir()
    return Backup(
        name,
        MagicMock(),
        src_dir,
        dst_dir,
        [HourlyTimeframe(keep=24, minutes=[0]), DailyTimeframe(keep=7, times=[(0, 0)])],
    )


def _create_backup_dir(backup: Backup, timeframe: str, hour: int, minute: int = 0) -> Path:
    assert isinstance(backup.dst_dir, Path)
    path = backup.dst_dir / (f"yaesm-{backup.name}-{timeframe}.2026_08_20_{hour:02}:{minute:02}")
    path.mkdir()
    return path


def test_add_argparser_arguments_with_positional_query():
    args = _parse_args(["foo", "after", "2026-01-01"])
    assert args.query == ["after", "2026-01-01"]
    assert args.additional_queries == []


def test_add_argparser_arguments_with_multiple_queries():
    args = _parse_args(["foo", "newest", "--query", "oldest", "--query", "closest", "12:30"])
    assert args.query == ["newest"]
    assert args.additional_queries == [["oldest"], ["closest", "12:30"]]


def test_add_argparser_arguments_with_only_optional_query():
    args = _parse_args(["foo", "--query", "after", "2026-01-01"])
    assert args.query == []
    assert args.additional_queries == [["after", "2026-01-01"]]


def test_add_argparser_arguments_with_multiple_optional_queries():
    args = _parse_args(["foo", "-q", "oldest", "-q", "closest", "12:30"])
    assert args.query == []
    assert args.additional_queries == [["oldest"], ["closest", "12:30"]]


def test_add_argparser_arguments_without_query():
    args = _parse_args(["foo"])
    assert args.query == []
    assert args.additional_queries == []
    assert args.timeframes == []


def test_add_argparser_arguments_normalizes_backup_names():
    args = _parse_args(["foo,,bar, foo, ,bar"])
    assert args.backup_names == ["foo", "bar"]


def test_add_argparser_arguments_with_repeated_timeframes():
    args = _parse_args(["foo", "--timeframes", "hourly", "--timeframes", "daily"])
    assert args.timeframes == ["hourly", "daily"]


def test_add_argparser_arguments_with_comma_separated_timeframes():
    args = _parse_args(["foo", "--timeframe", "hourly,,daily,hourly", "-t", "weekly"])
    assert args.timeframes == ["hourly", "daily", "weekly"]


def test_add_argparser_arguments_rejects_invalid_timeframe():
    with pytest.raises(SystemExit):
        _parse_args(["foo", "--timeframe", "hourly,invalid"])


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
        (["after", "2026-07-04T14:30"], FindQuery.Type.AFTER, datetime(2026, 7, 4, 14, 30)),
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
        now=datetime(2026, 8, 20, 12, 0),
    )
    assert query.type is FindQuery.Type.BETWEEN
    assert query.start == datetime(2026, 8, 17, 12, 0)
    assert query.end == datetime(2026, 8, 20, 10, 0)


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
        "now-2w",
        "2026-02-29",
        "2026-08-20T24:00",
        "8:15",
    ],
)
def test_find_query_rejects_invalid_times(value):
    with pytest.raises(FindQueryError, match="invalid time"):
        FindQuery(["after", value], now=datetime(2026, 8, 20, 12, 0))


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (["all"], [_snapshot(12), _snapshot(11), _snapshot(10), _snapshot(9)]),
        (["newest"], [_snapshot(12)]),
        (["oldest"], [_snapshot(9)]),
    ],
)
def test_find_query_selects_by_position(tokens, expected):
    snapshots = [_snapshot(12), _snapshot(11), _snapshot(10), _snapshot(9)]
    assert FindQuery(tokens).select(snapshots) == expected


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (["after", "2026-08-20T10:00"], [_snapshot(12), _snapshot(11)]),
        (["before", "2026-08-20T11:00"], [_snapshot(10), _snapshot(9)]),
        (
            ["between", "2026-08-20T10:00", "2026-08-20T11:00"],
            [_snapshot(11), _snapshot(10)],
        ),
        (
            ["between", "2026-08-20T11:00", "2026-08-20T10:00"],
            [_snapshot(11), _snapshot(10)],
        ),
        (["between", "2026-08-20T10:00", "2026-08-20T10:00"], [_snapshot(10)]),
        (["closest", "2026-08-20T10:30"], [_snapshot(11)]),
        (["closest", "2026-08-20T10:00"], [_snapshot(10)]),
        (["closest", "2026-08-20T13:00"], [_snapshot(12)]),
        (["closest", "2026-08-20T08:00"], [_snapshot(9)]),
    ],
)
def test_find_query_selects_by_time(tokens, expected):
    snapshots = [_snapshot(12), _snapshot(11), _snapshot(10), _snapshot(9)]
    assert FindQuery(tokens).select(snapshots) == expected


@pytest.mark.parametrize(
    "tokens",
    [
        ["after", "2026-08-20T13:00"],
        ["before", "2026-08-20T08:00"],
        ["between", "2026-08-20T07:00", "2026-08-20T08:00"],
    ],
)
def test_find_query_selects_nothing_from_nonempty_list(tokens):
    snapshots = [_snapshot(12), _snapshot(11), _snapshot(10), _snapshot(9)]
    assert FindQuery(tokens).select(snapshots) == []


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


def test_find_main_defaults_to_all(tmp_path, capsys):
    backup = _make_backup(tmp_path, "foo")
    newest = _create_backup_dir(backup, "hourly", 12)
    oldest = _create_backup_dir(backup, "daily", 10)

    assert FindSubcommand().main([backup], _parse_args(["foo"])) == 0
    assert capsys.readouterr().out.splitlines() == [str(newest), str(oldest)]


def test_find_main_combines_queries_without_duplicates(tmp_path, capsys):
    backup = _make_backup(tmp_path, "foo")
    newest = _create_backup_dir(backup, "hourly", 12)
    next_newest = _create_backup_dir(backup, "hourly", 11)
    _create_backup_dir(backup, "hourly", 10)

    args = _parse_args(["foo", "newest", "--query", "after", "2026-08-20T10:00"])
    assert FindSubcommand().main([backup], args) == 0
    assert capsys.readouterr().out.splitlines() == [str(newest), str(next_newest)]


def test_find_main_optional_queries_do_not_implicitly_add_all(tmp_path, capsys):
    backup = _make_backup(tmp_path, "foo")
    _create_backup_dir(backup, "hourly", 12)
    closest = _create_backup_dir(backup, "hourly", 11)
    oldest = _create_backup_dir(backup, "hourly", 10)

    args = _parse_args(["foo", "--query", "oldest", "--query", "closest", "2026-08-20T11:00"])
    assert FindSubcommand().main([backup], args) == 0
    assert capsys.readouterr().out.splitlines() == [str(closest), str(oldest)]


def test_find_main_supports_multiple_backup_names(tmp_path, capsys):
    foo = _make_backup(tmp_path, "foo")
    bar = _make_backup(tmp_path, "bar")
    foo_snapshot = _create_backup_dir(foo, "hourly", 12)
    bar_snapshot = _create_backup_dir(bar, "hourly", 11)

    assert FindSubcommand().main([bar, foo], _parse_args(["foo,bar", "all"])) == 0
    assert capsys.readouterr().out.splitlines() == [str(foo_snapshot), str(bar_snapshot)]


def test_find_main_supports_remote_backups(monkeypatch, capsys):
    target = SSHTarget("ssh://backup.example:/backups", Path("/key"))
    backup = Backup(
        "foo",
        MagicMock(),
        Path("/source"),
        target,
        [HourlyTimeframe(keep=24, minutes=[0])],
    )
    snapshot = target.with_path(_snapshot(12))
    collect = MagicMock(return_value=[snapshot])
    monkeypatch.setattr(bckp, "backups_collect", collect)

    args = _parse_args(["foo", "after", "2026-08-20T11:00"])
    assert FindSubcommand().main([backup], args) == 0
    assert capsys.readouterr().out.splitlines() == [str(snapshot.path)]
    collect.assert_called_once_with(backup, timeframes=None)


def test_find_main_filters_multiple_timeframes_including_immediate(tmp_path, capsys):
    backup = _make_backup(tmp_path, "foo")
    immediate = _create_backup_dir(backup, "immediate", 12)
    daily = _create_backup_dir(backup, "daily", 11)
    _create_backup_dir(backup, "hourly", 10)

    args = _parse_args(["foo", "all", "--timeframes", "daily,immediate"])
    assert FindSubcommand().main([backup], args) == 0
    assert capsys.readouterr().out.splitlines() == [str(immediate), str(daily)]


def test_find_main_returns_no_results_for_unconfigured_timeframe(tmp_path, capsys):
    backup = _make_backup(tmp_path, "foo")
    _create_backup_dir(backup, "hourly", 12)

    args = _parse_args(["foo", "all", "--timeframe", "weekly"])
    assert FindSubcommand().main([backup], args) == 0
    assert capsys.readouterr().out == ""


def test_find_main_rejects_unknown_backup_name(tmp_path, caplog):
    backup = _make_backup(tmp_path, "foo")
    caplog.set_level(logging.ERROR)

    assert FindSubcommand().main([backup], _parse_args(["missing"])) == 2
    assert "no backup named 'missing' in config" in caplog.text


def test_find_main_rejects_empty_backup_names(tmp_path, caplog):
    backup = _make_backup(tmp_path, "foo")
    caplog.set_level(logging.ERROR)

    assert FindSubcommand().main([backup], _parse_args([","])) == 2
    assert "no backup names specified" in caplog.text


def test_find_main_rejects_invalid_query(tmp_path, caplog):
    backup = _make_backup(tmp_path, "foo")
    caplog.set_level(logging.ERROR)

    assert FindSubcommand().main([backup], _parse_args(["foo", "after"])) == 2
    assert "query error: invalid query" in caplog.text
