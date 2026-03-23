import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from freezegun import freeze_time

import yaesm.backup as bckp
from yaesm.backend.rsyncbackend import RsyncBackend
from yaesm.subcommand.backupsubcommand import BackupSubcommand
from yaesm.timeframe import ImmediateTimeframe


@pytest.fixture
def backupsubcommand():
    return BackupSubcommand()


@pytest.fixture(scope="session")
def rsync_backend():
    return RsyncBackend()


def _parse_args(argv):
    parser = argparse.ArgumentParser()
    BackupSubcommand.add_argparser_arguments(parser)
    return parser.parse_args(argv)


def test_name():
    assert BackupSubcommand.name() == "backup"


def test_add_argparser_arguments():
    args = _parse_args(["mybackup"])
    assert args.backup_name == "mybackup"
    assert args.keep is None


def test_add_argparser_arguments_with_keep():
    args = _parse_args(["mybackup", "--keep", "5"])
    assert args.backup_name == "mybackup"
    assert args.keep == 5


def test_backup_name_not_found(backupsubcommand, caplog):
    caplog.set_level(logging.ERROR)
    args = _parse_args(["nonexistent"])
    assert backupsubcommand.main([], args) == 1
    assert "backup not found: nonexistent" in caplog.text


def test_selects_correct_backup_from_multiple(backupsubcommand, caplog):
    caplog.set_level(logging.INFO)
    backup_a = MagicMock()
    backup_a.name = "alpha"
    backup_b = MagicMock()
    backup_b.name = "bravo"
    backup_c = MagicMock()
    backup_c.name = "charlie"
    args = _parse_args(["bravo"])
    assert backupsubcommand.main([backup_a, backup_b, backup_c], args) == 0
    backup_a.backend.do_backup.assert_not_called()
    backup_b.backend.do_backup.assert_called_once()
    backup_c.backend.do_backup.assert_not_called()
    assert backup_b.backend.do_backup.call_args[0][0] is backup_b


def test_successful_backup(backupsubcommand, caplog):
    caplog.set_level(logging.INFO)
    backup = MagicMock()
    backup.name = "mybackup"
    args = _parse_args(["mybackup"])
    assert backupsubcommand.main([backup], args) == 0
    backup.backend.do_backup.assert_called_once()
    call_args = backup.backend.do_backup.call_args
    assert call_args[0][0] is backup
    tf = call_args[0][1]
    assert isinstance(tf, ImmediateTimeframe)
    assert tf.name == "immediate"
    assert tf.keep == sys.maxsize
    assert "starting backup" in caplog.text
    assert "completed successfully" in caplog.text


def test_successful_backup_with_keep(backupsubcommand, caplog):
    caplog.set_level(logging.INFO)
    backup = MagicMock()
    backup.name = "mybackup"
    args = _parse_args(["mybackup", "--keep", "3"])
    assert backupsubcommand.main([backup], args) == 0
    backup.backend.do_backup.assert_called_once()
    tf = backup.backend.do_backup.call_args[0][1]
    assert isinstance(tf, ImmediateTimeframe)
    assert tf.keep == 3


def test_backup_backend_exception(backupsubcommand, caplog):
    caplog.set_level(logging.ERROR)
    backup = MagicMock()
    backup.name = "mybackup"
    backup.backend.do_backup.side_effect = RuntimeError("boom")
    args = _parse_args(["mybackup"])
    assert backupsubcommand.main([backup], args) == 1
    assert "failed" in caplog.text


def test_backup_creates_immediate_named_directory(
    backupsubcommand, rsync_backend, path_generator, caplog
):
    caplog.set_level(logging.INFO)
    src_dir = path_generator("naming-src", mkdir=True)
    dst_dir = path_generator("naming-dst", mkdir=True)
    backup = bckp.Backup("testbackup", rsync_backend, src_dir, dst_dir, [])
    args = _parse_args([backup.name])
    with freeze_time("2026-03-23 14:30"):
        assert backupsubcommand.main([backup], args) == 0
    immediate_tf = ImmediateTimeframe(keep=sys.maxsize)
    backups = bckp.backups_collect(backup, timeframe=immediate_tf)
    assert len(backups) == 1
    assert isinstance(backups[0], Path)
    assert backups[0].name == "yaesm-testbackup-immediate.2026_03_23_14:30"


def test_keep_deletes_old_immediate_backups(
    backupsubcommand, rsync_backend, path_generator, caplog
):
    caplog.set_level(logging.INFO)
    keep = 2
    total = 5
    src_dir = path_generator("keep-src", mkdir=True)
    dst_dir = path_generator("keep-dst", mkdir=True)
    backup = bckp.Backup("keeptest", rsync_backend, src_dir, dst_dir, [])
    args = _parse_args([backup.name, "--keep", str(keep)])
    now = datetime.now()
    for i in range(total):
        with freeze_time(now + timedelta(hours=i)):
            assert backupsubcommand.main([backup], args) == 0
    immediate_tf = ImmediateTimeframe(keep=keep)
    remaining = bckp.backups_collect(backup, timeframe=immediate_tf)
    assert len(remaining) == keep
    # remaining should be the newest backups, sorted newest to oldest
    for i, b in enumerate(remaining):
        assert isinstance(b, Path)
        expected_time = now + timedelta(hours=total - 1 - i)
        expected_basename = expected_time.strftime(f"yaesm-{backup.name}-immediate.%Y_%m_%d_%H:%M")
        assert b.name == expected_basename
