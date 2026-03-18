"""tests/test_yaesm/test_subcommand/test_checksubcommand.py."""

import argparse
import logging
from unittest.mock import MagicMock

import pytest

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
    assert args.backup_name is None
    args = parser.parse_args(["mybackup"])
    assert args.backup_name == "mybackup"


def test_check_all_backups_pass(checksubcommand, capsys):
    backups = [_make_backup("a", []), _make_backup("b", [])]
    parser = argparse.ArgumentParser()
    CheckSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args([])
    rc = checksubcommand.main(backups, args)
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    for b in backups:
        b.backend.check.assert_called_once_with(b)


def test_check_some_fail(checksubcommand, capsys):
    backups = [_make_backup("good-backup", []), _make_backup("bad-backup", ["err1", "err2"])]
    parser = argparse.ArgumentParser()
    CheckSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args([])
    rc = checksubcommand.main(backups, args)
    assert rc == 1
    out = capsys.readouterr().out
    assert "bad-backup" in out
    assert "err1" in out
    assert "err2" in out
    assert "good-backup" not in out


def test_check_multiple_errors_one_backup(checksubcommand, capsys):
    errors = ["src_dir does not exist", "dst_dir does not exist", "required tool not found"]
    backups = [_make_backup("mybackup", errors)]
    parser = argparse.ArgumentParser()
    CheckSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args([])
    rc = checksubcommand.main(backups, args)
    assert rc == 1
    out = capsys.readouterr().out
    assert "mybackup" in out
    for err in errors:
        assert err in out


def test_check_specific_backup(checksubcommand):
    backup_a = _make_backup("a", [])
    backup_b = _make_backup("b", [])
    backups = [backup_a, backup_b]
    parser = argparse.ArgumentParser()
    CheckSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args(["a"])
    rc = checksubcommand.main(backups, args)
    assert rc == 0
    backup_a.backend.check.assert_called_once_with(backup_a)
    backup_b.backend.check.assert_not_called()


def test_check_unknown_backup_name(checksubcommand, caplog):
    backups = [_make_backup("a", [])]
    parser = argparse.ArgumentParser()
    CheckSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args(["nonexistent"])
    caplog.set_level(logging.ERROR)
    rc = checksubcommand.main(backups, args)
    assert rc == 1
    assert "nonexistent" in caplog.text
