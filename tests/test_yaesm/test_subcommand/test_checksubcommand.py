"""tests/test_yaesm/test_subcommand/test_checksubcommand.py."""

import argparse
import logging
from unittest.mock import MagicMock

import pytest

from yaesm.backend.backendbase import CheckResult
from yaesm.backup import Backup
from yaesm.subcommand.checksubcommand import CheckSubcommand


@pytest.fixture
def checksubcommand():
    return CheckSubcommand()


def _make_backup(name, check_return):
    backend = MagicMock()
    backend.check.return_value = check_return
    backup = MagicMock(spec=Backup)
    backup.name = name
    backup.backend = backend
    return backup


def test_add_argparser_arguments():
    parser = argparse.ArgumentParser()
    CheckSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args([])
    assert args.backup_names is None
    assert not args.quiet
    args = parser.parse_args(["mybackup"])
    assert args.backup_names == ["mybackup"]
    args = parser.parse_args(["alpha,,bravo, alpha, ,bravo"])
    assert args.backup_names == ["alpha", "bravo"]
    args = parser.parse_args(["--quiet"])
    assert args.quiet


def test_check_all_backups_pass(checksubcommand, capsys):
    backups = [
        _make_backup("a", [CheckResult("preconditions")]),
        _make_backup("b", [CheckResult("preconditions")]),
    ]
    parser = argparse.ArgumentParser()
    CheckSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args([])
    rc = checksubcommand.main(backups, args)
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "backup: a\n    PASS  preconditions\nbackup: b\n    PASS  preconditions\n"
    )
    assert captured.err == ""
    for b in backups:
        b.backend.check.assert_called_once_with(b)


def test_check_some_fail(checksubcommand, capsys):
    backups = [
        _make_backup("good-backup", [CheckResult("preconditions")]),
        _make_backup("bad-backup", [CheckResult("preconditions", ("err1", "err2"))]),
    ]
    parser = argparse.ArgumentParser()
    CheckSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args(["--quiet"])
    rc = checksubcommand.main(backups, args)
    assert rc == 1
    out = capsys.readouterr().out
    assert "bad-backup" in out
    assert "err1" in out
    assert "err2" in out
    assert "good-backup" not in out


def test_check_results_by_default(checksubcommand, capsys):
    backup = _make_backup(
        "mybackup",
        [CheckResult("source exists"), CheckResult("destination exists", ("destination failed",))],
    )
    parser = argparse.ArgumentParser()
    CheckSubcommand.add_argparser_arguments(parser)
    rc = checksubcommand.main([backup], parser.parse_args([]))
    assert rc == 1
    assert capsys.readouterr().out == (
        "backup: mybackup\n    PASS  source exists\n    FAIL  destination failed\n"
    )


def test_failed_check_printed_once(checksubcommand, capsys):
    error = "dst_dir is not on a btrfs filesystem: /backups"
    backup = _make_backup(
        "mybackup",
        [CheckResult("dst_dir is on a btrfs filesystem: /backups", (error,))],
    )
    parser = argparse.ArgumentParser()
    CheckSubcommand.add_argparser_arguments(parser)

    assert checksubcommand.main([backup], parser.parse_args([])) == 1
    assert capsys.readouterr().out == f"backup: mybackup\n    FAIL  {error}\n"


def test_check_multiple_errors_one_backup(checksubcommand, capsys):
    errors = ["src_dir does not exist", "dst_dir does not exist", "required tool not found"]
    backups = [_make_backup("mybackup", [CheckResult("preconditions", tuple(errors))])]
    parser = argparse.ArgumentParser()
    CheckSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args([])
    rc = checksubcommand.main(backups, args)
    assert rc == 1
    out = capsys.readouterr().out
    assert "mybackup" in out
    for err in errors:
        assert err in out


def test_check_specific_backups(checksubcommand):
    backup_a = _make_backup("a", [])
    backup_b = _make_backup("b", [])
    backup_c = _make_backup("c", [])
    backups = [backup_a, backup_b, backup_c]
    parser = argparse.ArgumentParser()
    CheckSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args(["b,a", "--quiet"])
    rc = checksubcommand.main(backups, args)
    assert rc == 0
    backup_a.backend.check.assert_called_once_with(backup_a)
    backup_b.backend.check.assert_called_once_with(backup_b)
    backup_c.backend.check.assert_not_called()


def test_check_unknown_backup_name(checksubcommand, caplog):
    backup = _make_backup("a", [])
    parser = argparse.ArgumentParser()
    CheckSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args(["a,nonexistent"])
    caplog.set_level(logging.ERROR)
    rc = checksubcommand.main([backup], args)
    assert rc == 2
    assert "nonexistent" in caplog.text
    backup.backend.check.assert_not_called()


def test_check_empty_backup_names(checksubcommand, caplog):
    parser = argparse.ArgumentParser()
    CheckSubcommand.add_argparser_arguments(parser)
    caplog.set_level(logging.ERROR)

    assert checksubcommand.main([], parser.parse_args([","])) == 2
    assert "no backup names specified" in caplog.text
