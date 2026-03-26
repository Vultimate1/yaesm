"""tests/test_yaesm/test_backend/test_btrfsbackend.py."""

import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import voluptuous as vlp
from freezegun import freeze_time

import yaesm.backend.btrfsbackend as btrfs
import yaesm.backup as bckp
from yaesm.backup import Backup
from yaesm.sshtarget import SSHTarget


@pytest.fixture(scope="session")
def btrfs_backend():
    return btrfs.BtrfsBackend()


def test_do_backup(btrfs_backend, random_backup_generator, path_generator):
    for backup_type in ["local_to_local", "local_to_remote,", "remote_to_local"]:
        backup = random_backup_generator(backend_type="btrfs", backup_type=backup_type)
        timeframe = backup.timeframes[0]
        timeframe.keep = 3
        now = datetime.now()
        expected_backup_basenames = []
        for i in range(timeframe.keep + 2):
            with freeze_time(now + timedelta(hours=i)):
                expected_backup_basenames.insert(0, bckp.backup_basename_now(backup, timeframe))
                btrfs_backend.do_backup(backup, timeframe)
        backups = bckp.backups_collect(backup, timeframe=timeframe)
        backup_basenames = list(
            map(lambda x: x.path.name if isinstance(x, SSHTarget) else x.name, backups)
        )
        assert len(backups) == timeframe.keep
        assert expected_backup_basenames[0 : timeframe.keep] == backup_basenames


def test_do_backup_already_exists(btrfs_backend, random_backup_generator):
    for backup_type in ["local_to_local", "local_to_remote", "remote_to_local"]:
        backup = random_backup_generator(backend_type="btrfs", backup_type=backup_type)
        timeframe = backup.timeframes[0]
        with freeze_time("2020-01-01 00:00"):
            btrfs_backend.do_backup(backup, timeframe)
            with pytest.raises(bckp.BackupError, match="backup already exists"):
                btrfs_backend.do_backup(backup, timeframe)


def test_exec_backup_local_to_local(btrfs_backend, random_backup_generator):
    with freeze_time("1999-05-13 23:59"):
        backup_diff_fs = random_backup_generator(backend_type="btrfs", backup_type="local_to_local")
        timeframe = backup_diff_fs.timeframes[0]
        backup_path = backup_diff_fs.dst_dir.joinpath(
            f"yaesm-{backup_diff_fs.name}-{timeframe.name}.1999_05_13_23:59"
        )
        assert not backup_path.is_dir()
        btrfs_backend._exec_backup_local_to_local(
            backup_diff_fs, bckp.backup_basename_now(backup_diff_fs, timeframe), timeframe
        )
        assert backup_path.is_dir()

    with freeze_time("1999-05-13 23:59"):
        backup_same_fs = random_backup_generator(backend_type="btrfs", backup_type="local_to_local")
        timeframe = backup_same_fs.timeframes[0]
        backup_path = backup_same_fs.dst_dir.joinpath(
            f"yaesm-{backup_same_fs.name}-{timeframe.name}.1999_05_13_23:59"
        )
        assert not backup_path.is_dir()
        btrfs_backend._exec_backup_local_to_local(
            backup_same_fs, bckp.backup_basename_now(backup_same_fs, timeframe), timeframe
        )
        assert backup_path.is_dir()


def test_exec_backup_local_to_remote(btrfs_backend, random_backup_generator):
    backup = random_backup_generator(backend_type="btrfs", backup_type="local_to_remote")
    timeframe = backup.timeframes[0]
    with freeze_time("1999-05-13 23:59"):
        backup_path = backup.dst_dir.path.joinpath(
            f"yaesm-{backup.name}-{timeframe.name}.1999_05_13_23:59"
        )
        assert not backup_path.is_dir()
        btrfs_backend._exec_backup_local_to_remote(
            backup, bckp.backup_basename_now(backup, timeframe), timeframe
        )
        assert backup_path.is_dir()


def test_exec_backup_remote_to_local(btrfs_backend, random_backup_generator):
    backup = random_backup_generator(backend_type="btrfs", backup_type="remote_to_local")
    timeframe = backup.timeframes[0]
    with freeze_time("1999-05-13 23:59"):
        backup_path = backup.dst_dir.joinpath(
            f"yaesm-{backup.name}-{timeframe.name}.1999_05_13_23:59"
        )
        assert not backup_path.is_dir()
        btrfs_backend._exec_backup_remote_to_local(
            backup, bckp.backup_basename_now(backup, timeframe), timeframe
        )
        assert backup_path.is_dir()


def test_btrfs_take_and_delete_snapshot_local(btrfs_fs, path_generator):
    dst_dir1 = path_generator("test-snapshot", base_dir=btrfs_fs, mkdir=True)
    dst_dir2 = path_generator("test-snapshot", base_dir=btrfs_fs, mkdir=True)
    backup_basename = "yaesm-foo-backup-hourly.1999_05_13_23:59"
    snapshot1 = dst_dir1.joinpath(backup_basename)
    snapshot2 = dst_dir2.joinpath(backup_basename)
    returncode1, snapshot1 = btrfs._btrfs_take_snapshot_local(btrfs_fs, snapshot1)
    returncode2, snapshot2 = btrfs._btrfs_take_snapshot_local(btrfs_fs, snapshot2)
    assert returncode1 == 0
    assert returncode2 == 0
    assert len(os.listdir(dst_dir1)) == 1
    assert len(os.listdir(dst_dir2)) == 1
    returncode, deleted = btrfs._btrfs_delete_subvolumes_local(snapshot1, snapshot2)
    assert returncode == 0
    assert [snapshot1, snapshot2] == deleted
    assert len(os.listdir(dst_dir1)) == 0
    assert len(os.listdir(dst_dir2)) == 0
    dst_dir = path_generator("test-snapshot", base_dir=btrfs_fs, mkdir=True).joinpath("foo")
    returncode, snapshot = btrfs._btrfs_take_snapshot_local(btrfs_fs, dst_dir)
    assert returncode == 0
    assert snapshot.is_dir()
    assert snapshot.name == "foo"
    returncode, deleted = btrfs._btrfs_delete_subvolumes_local(snapshot)
    assert returncode == 0
    assert [snapshot] == deleted
    assert not snapshot.is_dir()
    assert len(os.listdir(dst_dir.parent)) == 0
    bad_src_dir = path_generator("bad-src-dir", mkdir=False)
    with pytest.raises(subprocess.CalledProcessError):
        btrfs._btrfs_take_snapshot_local(bad_src_dir, Path("/foo"))
    with pytest.raises(subprocess.CalledProcessError):
        btrfs._btrfs_delete_subvolumes_local(bad_src_dir, Path("/foo"))


def test_btrfs_take_and_delete_snapshot_remote(btrfs_fs, sshtarget, path_generator):
    src_dir = sshtarget.with_path(btrfs_fs)
    dst_dir1 = sshtarget.with_path(path_generator("test-snapshot", base_dir=btrfs_fs, mkdir=True))
    dst_dir2 = sshtarget.with_path(path_generator("test-snapshot", base_dir=btrfs_fs, mkdir=True))
    backup_basename = "yaesm-foo-backup-hourly.1999_05_13_23:59"
    snapshot1 = dst_dir1.with_path(dst_dir1.path.joinpath(backup_basename))
    snapshot2 = dst_dir2.with_path(dst_dir2.path.joinpath(backup_basename))
    returncode1, snapshot1 = btrfs._btrfs_take_snapshot_remote(src_dir, snapshot1)
    returncode2, snapshot2 = btrfs._btrfs_take_snapshot_remote(src_dir, snapshot2)
    assert returncode1 == 0
    assert returncode2 == 0
    assert bckp.backup_basename_re().match(snapshot1.path.name)
    assert bckp.backup_basename_re().match(snapshot2.path.name)
    assert len(os.listdir(dst_dir1.path)) == 1
    assert len(os.listdir(dst_dir2.path)) == 1
    returncode, deleted = btrfs._btrfs_delete_subvolumes_remote(snapshot1, snapshot2)
    assert returncode == 0
    assert [snapshot1, snapshot2] == deleted
    assert len(os.listdir(dst_dir1.path)) == 0
    assert len(os.listdir(dst_dir2.path)) == 0
    snapshot = sshtarget.with_path(
        path_generator("test-snapshot", base_dir=btrfs_fs, mkdir=True).joinpath("foo")
    )
    returncode, snapshot = btrfs._btrfs_take_snapshot_remote(src_dir, snapshot)
    assert returncode == 0
    assert snapshot.path.is_dir()
    assert snapshot.path.name == "foo"
    returncode, deleted = btrfs._btrfs_delete_subvolumes_remote(snapshot)
    assert returncode == 0
    assert [snapshot] == deleted
    assert not snapshot.path.is_dir()
    assert len(os.listdir(snapshot.path.parent)) == 0


def test_delete_backups_local(btrfs_backend, btrfs_fs, path_generator):
    dst_dir1 = path_generator("test-snapshot", base_dir=btrfs_fs, mkdir=True)
    dst_dir2 = path_generator("test-snapshot", base_dir=btrfs_fs, mkdir=True)
    backup_basename = "yaesm-foo-backup-hourly.1999_05_13_23:59"
    snapshot1 = dst_dir1.joinpath(backup_basename)
    snapshot2 = dst_dir2.joinpath(backup_basename)
    _, snapshot1 = btrfs._btrfs_take_snapshot_local(btrfs_fs, snapshot1)
    _, snapshot2 = btrfs._btrfs_take_snapshot_local(btrfs_fs, snapshot2)
    assert Path(snapshot1).is_dir()
    assert Path(snapshot2).is_dir()
    btrfs_backend._delete_backups_local(snapshot1, snapshot2)
    assert not Path(snapshot1).is_dir()
    assert not Path(snapshot2).is_dir()


def test_delete_backups_remote(btrfs_backend, btrfs_fs, sshtarget, path_generator):
    src_dir = sshtarget.with_path(btrfs_fs)
    dst_dir1 = sshtarget.with_path(path_generator("test-snapshot", base_dir=btrfs_fs, mkdir=True))
    dst_dir2 = sshtarget.with_path(path_generator("test-snapshot", base_dir=btrfs_fs, mkdir=True))
    backup_basename = "yaesm-foo-backup-hourly.1999_05_13_23:59"
    snapshot1 = dst_dir1.with_path(dst_dir1.path.joinpath(backup_basename))
    snapshot2 = dst_dir2.with_path(dst_dir2.path.joinpath(backup_basename))
    _, snapshot1 = btrfs._btrfs_take_snapshot_remote(src_dir, snapshot1)
    _, snapshot2 = btrfs._btrfs_take_snapshot_remote(src_dir, snapshot2)
    assert snapshot1.path.is_dir()
    assert snapshot2.path.is_dir()
    btrfs_backend._delete_backups_remote(snapshot1, snapshot2)
    assert not snapshot1.path.is_dir()
    assert not snapshot2.path.is_dir()


def test_btrfs_send_receive_local_to_local(btrfs_fs, path_generator):
    _, parent_snapshot = btrfs._btrfs_take_snapshot_local(
        btrfs_fs, path_generator("test-parent-snapshot", base_dir=btrfs_fs)
    )
    receive_dir = path_generator("test-btrfs-receive-dst", base_dir=btrfs_fs, mkdir=True)
    returncode, received_snapshot = btrfs._btrfs_send_receive_local_to_local(
        parent_snapshot, receive_dir
    )
    assert returncode == 0
    assert received_snapshot.is_dir()
    assert received_snapshot == receive_dir.joinpath(parent_snapshot.name)
    _, tmp_snapshot = btrfs._btrfs_take_snapshot_local(
        btrfs_fs, path_generator("test-tmp-snapshot", base_dir=btrfs_fs)
    )
    returncode, received_snapshot = btrfs._btrfs_send_receive_local_to_local(
        tmp_snapshot, receive_dir, parent=parent_snapshot
    )
    assert returncode == 0
    assert received_snapshot.is_dir()
    assert received_snapshot == receive_dir.joinpath(tmp_snapshot.name)


def test_btrfs_send_receive_local_to_remote(btrfs_fs, sshtarget, path_generator):
    _, parent_snapshot = btrfs._btrfs_take_snapshot_local(
        btrfs_fs, path_generator("test-parent-snapshot", base_dir=btrfs_fs)
    )
    receive_dir = sshtarget.with_path(
        path_generator("test-btrfs-receive-dst", base_dir=btrfs_fs, mkdir=True)
    )
    returncode, received_snapshot = btrfs._btrfs_send_receive_local_to_remote(
        parent_snapshot, receive_dir
    )
    assert returncode == 0
    assert received_snapshot.path.is_dir()
    assert received_snapshot.path == receive_dir.path.joinpath(parent_snapshot.name)
    _, tmp_snapshot = btrfs._btrfs_take_snapshot_local(
        btrfs_fs, path_generator("test-tmp-snapshot", base_dir=btrfs_fs)
    )
    returncode, received_snapshot = btrfs._btrfs_send_receive_local_to_remote(
        tmp_snapshot, receive_dir, parent=parent_snapshot
    )
    assert returncode == 0
    assert received_snapshot.path.is_dir()
    assert received_snapshot.path == receive_dir.path.joinpath(tmp_snapshot.name)


def test_btrfs_send_receive_remote_to_local(btrfs_fs, sshtarget, path_generator):
    _, parent_snapshot = btrfs._btrfs_take_snapshot_local(
        btrfs_fs, path_generator("test-parent-snapshot", base_dir=btrfs_fs)
    )
    parent_snapshot = sshtarget.with_path(parent_snapshot)
    receive_dir = path_generator("test-btrfs-receive-dst", base_dir=btrfs_fs, mkdir=True)
    returncode, received_snapshot = btrfs._btrfs_send_receive_remote_to_local(
        parent_snapshot, receive_dir
    )
    assert returncode == 0
    assert received_snapshot.is_dir()
    assert received_snapshot == receive_dir.joinpath(parent_snapshot.path.name)
    _, tmp_snapshot = btrfs._btrfs_take_snapshot_local(
        btrfs_fs, path_generator("test-tmp-snapshot", base_dir=btrfs_fs)
    )
    tmp_snapshot = sshtarget.with_path(tmp_snapshot)
    returncode, received_snapshot = btrfs._btrfs_send_receive_remote_to_local(
        tmp_snapshot, receive_dir, parent=parent_snapshot
    )
    assert returncode == 0
    assert received_snapshot.is_dir()
    assert received_snapshot == receive_dir.joinpath(tmp_snapshot.path.name)


def test_btrfs_bootstrap_local_to_local(btrfs_fs, path_generator):
    src_dir = btrfs_fs
    dst_dir = path_generator("test-btrfs-bootstrap-dst", base_dir=btrfs_fs, mkdir=True)
    dst_bootstrap = dst_dir.joinpath(btrfs._btrfs_bootstrap_snapshot_basename())
    bootstrap_snapshot = btrfs._btrfs_bootstrap_local_to_local(src_dir, dst_dir)
    assert bootstrap_snapshot == src_dir.joinpath(btrfs._btrfs_bootstrap_snapshot_basename())
    assert bootstrap_snapshot.is_dir()
    assert dst_bootstrap.is_dir()
    btrfs._btrfs_delete_subvolumes_local(dst_bootstrap)
    assert not dst_bootstrap.is_dir()
    bootstrap_snapshot = btrfs._btrfs_bootstrap_local_to_local(src_dir, dst_dir)
    assert dst_bootstrap.is_dir()
    btrfs._btrfs_delete_subvolumes_local(bootstrap_snapshot)
    assert not bootstrap_snapshot.is_dir()
    btrfs._btrfs_bootstrap_local_to_local(src_dir, dst_dir)
    assert bootstrap_snapshot.is_dir()
    btrfs._btrfs_bootstrap_local_to_local(src_dir, dst_dir)
    assert bootstrap_snapshot.is_dir()


def test_btrfs_bootstrap_local_to_remote(btrfs_fs, sshtarget, path_generator):
    src_dir = btrfs_fs
    dst_dir = sshtarget.with_path(
        path_generator("test-btrfs-bootstrap-dst", base_dir=btrfs_fs, mkdir=True)
    )
    dst_bootstrap = dst_dir.with_path(
        dst_dir.path.joinpath(btrfs._btrfs_bootstrap_snapshot_basename())
    )
    bootstrap_snapshot = btrfs._btrfs_bootstrap_local_to_remote(src_dir, dst_dir)
    assert bootstrap_snapshot == src_dir.joinpath(btrfs._btrfs_bootstrap_snapshot_basename())
    assert bootstrap_snapshot.is_dir()
    assert dst_bootstrap.path.is_dir()
    btrfs._btrfs_delete_subvolumes_local(dst_bootstrap.path)
    assert not dst_bootstrap.path.is_dir()
    bootstrap_snapshot = btrfs._btrfs_bootstrap_local_to_remote(src_dir, dst_dir)
    assert dst_bootstrap.path.is_dir()
    btrfs._btrfs_delete_subvolumes_local(bootstrap_snapshot)
    assert not bootstrap_snapshot.is_dir()
    btrfs._btrfs_bootstrap_local_to_remote(src_dir, dst_dir)
    assert bootstrap_snapshot.is_dir()
    btrfs._btrfs_bootstrap_local_to_remote(src_dir, dst_dir)
    assert bootstrap_snapshot.is_dir()


def test_btrfs_bootstrap_remote_to_local(btrfs_fs, sshtarget, path_generator):
    src_dir = sshtarget.with_path(btrfs_fs)
    dst_dir = path_generator("test-btrfs-bootstrap-dst", base_dir=btrfs_fs, mkdir=True)
    dst_bootstrap = dst_dir.joinpath(btrfs._btrfs_bootstrap_snapshot_basename())
    bootstrap_snapshot = btrfs._btrfs_bootstrap_remote_to_local(src_dir, dst_dir)
    assert bootstrap_snapshot.path == src_dir.path.joinpath(
        btrfs._btrfs_bootstrap_snapshot_basename()
    )
    assert bootstrap_snapshot.path.is_dir()
    assert dst_bootstrap.is_dir()
    btrfs._btrfs_delete_subvolumes_local(dst_bootstrap)
    assert not dst_bootstrap.is_dir()
    bootstrap_snapshot = btrfs._btrfs_bootstrap_remote_to_local(src_dir, dst_dir)
    assert dst_bootstrap.is_dir()
    btrfs._btrfs_delete_subvolumes_remote(bootstrap_snapshot)
    assert not bootstrap_snapshot.path.is_dir()
    btrfs._btrfs_bootstrap_remote_to_local(src_dir, dst_dir)
    assert bootstrap_snapshot.path.is_dir()
    btrfs._btrfs_bootstrap_remote_to_local(src_dir, dst_dir)
    assert bootstrap_snapshot.path.is_dir()


# --- check: local_to_local ---


def test_check_local_to_local_pass(btrfs_backend, btrfs_fs_generator, random_timeframes_generator):
    src_dir = btrfs_fs_generator()
    dst_dir = btrfs_fs_generator()
    backup = Backup("test", btrfs_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = btrfs_backend.check(backup)
    assert errors == []


def test_check_local_to_local_src_dir_missing(
    btrfs_backend, btrfs_fs_generator, path_generator, random_timeframes_generator
):
    src_dir = path_generator("nonexistent-src")
    dst_dir = btrfs_fs_generator()
    backup = Backup("test", btrfs_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = btrfs_backend.check(backup)
    assert any("src_dir" in e and "does not exist" in e for e in errors)


def test_check_local_to_local_dst_dir_missing(
    btrfs_backend, btrfs_fs_generator, path_generator, random_timeframes_generator
):
    src_dir = btrfs_fs_generator()
    dst_dir = path_generator("nonexistent-dst")
    backup = Backup("test", btrfs_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = btrfs_backend.check(backup)
    assert any("dst_dir" in e and "does not exist" in e for e in errors)


def test_check_local_to_local_tool_missing(
    monkeypatch, btrfs_backend, btrfs_fs_generator, random_timeframes_generator
):
    src_dir = btrfs_fs_generator()
    dst_dir = btrfs_fs_generator()
    backup = Backup("test", btrfs_backend, src_dir, dst_dir, random_timeframes_generator())
    monkeypatch.setattr(shutil, "which", lambda _tool: None)
    errors = btrfs_backend.check(backup)
    assert any("not found locally" in e and "btrfs" in e for e in errors)


def test_check_local_to_local_src_not_btrfs(
    btrfs_backend, btrfs_fs_generator, path_generator, random_timeframes_generator
):
    src_dir = path_generator("non-btrfs-src", mkdir=True)
    dst_dir = btrfs_fs_generator()
    backup = Backup("test", btrfs_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = btrfs_backend.check(backup)
    assert any("src_dir" in e and "btrfs" in e.lower() for e in errors)


def test_check_local_to_local_dst_not_btrfs(
    btrfs_backend, btrfs_fs_generator, path_generator, random_timeframes_generator
):
    src_dir = btrfs_fs_generator()
    dst_dir = path_generator("non-btrfs-dst", mkdir=True)
    backup = Backup("test", btrfs_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = btrfs_backend.check(backup)
    assert any("dst_dir" in e and "btrfs" in e.lower() for e in errors)


# --- check: local_to_remote ---


def test_check_local_to_remote_pass(
    btrfs_backend, btrfs_fs_generator, sshtarget_generator, random_timeframes_generator
):
    src_dir = btrfs_fs_generator()
    dst_dir_path = btrfs_fs_generator()
    sshtarget = sshtarget_generator()
    dst_dir = sshtarget.with_path(dst_dir_path)
    backup = Backup("test", btrfs_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = btrfs_backend.check(backup)
    assert errors == []


def test_check_local_to_remote_ssh_fail(
    btrfs_backend,
    btrfs_fs_generator,
    sshtarget_generator,
    path_generator,
    random_timeframes_generator,
):
    src_dir = btrfs_fs_generator()
    dst_dir_path = btrfs_fs_generator()
    sshtarget = sshtarget_generator()
    dst_dir = sshtarget.with_path(dst_dir_path)
    dst_dir.key = path_generator("bad-key", touch=True)
    dst_dir.user = "nonexistent-user"
    backup = Backup("test", btrfs_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = btrfs_backend.check(backup)
    assert any("SSH" in e or "ssh" in e or "cannot" in e.lower() for e in errors)


def test_check_local_to_remote_remote_dir_missing(
    btrfs_backend,
    btrfs_fs_generator,
    sshtarget_generator,
    path_generator,
    random_timeframes_generator,
):
    src_dir = btrfs_fs_generator()
    sshtarget = sshtarget_generator()
    dst_dir = sshtarget.with_path(path_generator("nonexistent-remote-dst"))
    backup = Backup("test", btrfs_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = btrfs_backend.check(backup)
    assert any("dst_dir" in e and "does not exist" in e for e in errors)


def test_check_local_to_remote_remote_tool_missing(
    monkeypatch, btrfs_backend, btrfs_fs_generator, sshtarget_generator, random_timeframes_generator
):
    src_dir = btrfs_fs_generator()
    dst_dir_path = btrfs_fs_generator()
    sshtarget = sshtarget_generator()
    dst_dir = sshtarget.with_path(dst_dir_path)
    backup = Backup("test", btrfs_backend, src_dir, dst_dir, random_timeframes_generator())
    original_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and "type btrfs" in " ".join(str(c) for c in cmd):
            return subprocess.CompletedProcess(cmd, returncode=1)
        return original_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    errors = btrfs_backend.check(backup)
    assert any("not found on remote" in e and "btrfs" in e for e in errors)


def test_check_local_to_remote_remote_not_btrfs(
    btrfs_backend,
    btrfs_fs_generator,
    sshtarget_generator,
    path_generator,
    random_timeframes_generator,
):
    src_dir = btrfs_fs_generator()
    sshtarget = sshtarget_generator()
    dst_dir = sshtarget.with_path(path_generator("non-btrfs-remote-dst", mkdir=True))
    backup = Backup("test", btrfs_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = btrfs_backend.check(backup)
    assert any("dst_dir" in e and "btrfs" in e.lower() for e in errors)


# --- check: remote_to_local ---


def test_check_remote_to_local_pass(
    btrfs_backend, btrfs_fs_generator, sshtarget_generator, random_timeframes_generator
):
    src_dir_path = btrfs_fs_generator()
    dst_dir = btrfs_fs_generator()
    sshtarget = sshtarget_generator()
    src_dir = sshtarget.with_path(src_dir_path)
    backup = Backup("test", btrfs_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = btrfs_backend.check(backup)
    assert errors == []


def test_check_remote_to_local_ssh_fail(
    btrfs_backend,
    btrfs_fs_generator,
    sshtarget_generator,
    path_generator,
    random_timeframes_generator,
):
    src_dir_path = btrfs_fs_generator()
    dst_dir = btrfs_fs_generator()
    sshtarget = sshtarget_generator()
    src_dir = sshtarget.with_path(src_dir_path)
    src_dir.key = path_generator("bad-key", touch=True)
    src_dir.user = "nonexistent-user"
    backup = Backup("test", btrfs_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = btrfs_backend.check(backup)
    assert any("SSH" in e or "ssh" in e or "cannot" in e.lower() for e in errors)


def test_check_remote_to_local_remote_dir_missing(
    btrfs_backend,
    btrfs_fs_generator,
    sshtarget_generator,
    path_generator,
    random_timeframes_generator,
):
    sshtarget = sshtarget_generator()
    src_dir = sshtarget.with_path(path_generator("nonexistent-remote-src"))
    dst_dir = btrfs_fs_generator()
    backup = Backup("test", btrfs_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = btrfs_backend.check(backup)
    assert any("src_dir" in e and "does not exist" in e for e in errors)


def test_check_remote_to_local_remote_tool_missing(
    monkeypatch, btrfs_backend, btrfs_fs_generator, sshtarget_generator, random_timeframes_generator
):
    src_dir_path = btrfs_fs_generator()
    dst_dir = btrfs_fs_generator()
    sshtarget = sshtarget_generator()
    src_dir = sshtarget.with_path(src_dir_path)
    backup = Backup("test", btrfs_backend, src_dir, dst_dir, random_timeframes_generator())
    original_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and "type btrfs" in " ".join(str(c) for c in cmd):
            return subprocess.CompletedProcess(cmd, returncode=1)
        return original_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    errors = btrfs_backend.check(backup)
    assert any("not found on remote" in e and "btrfs" in e for e in errors)


def test_check_remote_to_local_remote_not_btrfs(
    btrfs_backend,
    btrfs_fs_generator,
    sshtarget_generator,
    path_generator,
    random_timeframes_generator,
):
    sshtarget = sshtarget_generator()
    src_dir = sshtarget.with_path(path_generator("non-btrfs-remote-src", mkdir=True))
    dst_dir = btrfs_fs_generator()
    backup = Backup("test", btrfs_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = btrfs_backend.check(backup)
    assert any("src_dir" in e and "btrfs" in e.lower() for e in errors)


def test_check_local_to_remote_remote_dst_not_writable(
    monkeypatch, btrfs_backend, btrfs_fs_generator, sshtarget_generator, random_timeframes_generator
):
    src_dir = btrfs_fs_generator()
    dst_dir_path = btrfs_fs_generator()
    sshtarget = sshtarget_generator()
    dst_dir = sshtarget.with_path(dst_dir_path)
    backup = Backup("test", btrfs_backend, src_dir, dst_dir, random_timeframes_generator())
    original_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and "test -w" in " ".join(str(c) for c in cmd):
            return subprocess.CompletedProcess(cmd, returncode=1)
        return original_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    errors = btrfs_backend.check(backup)
    assert any("dst_dir" in e and "not writable" in e for e in errors)


def test_check_remote_to_local_remote_src_not_readable(
    monkeypatch, btrfs_backend, btrfs_fs_generator, sshtarget_generator, random_timeframes_generator
):
    src_dir_path = btrfs_fs_generator()
    dst_dir = btrfs_fs_generator()
    sshtarget = sshtarget_generator()
    src_dir = sshtarget.with_path(src_dir_path)
    backup = Backup("test", btrfs_backend, src_dir, dst_dir, random_timeframes_generator())
    original_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and "test -r" in " ".join(str(c) for c in cmd):
            return subprocess.CompletedProcess(cmd, returncode=1)
        return original_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    errors = btrfs_backend.check(backup)
    assert any("src_dir" in e and "not readable" in e for e in errors)


# --- config_schema ---


def test_config_schema():
    schema = btrfs.BtrfsBackend.config_schema()
    # accepts valid positive int and sets it on the backend instance
    backend = btrfs.BtrfsBackend()
    data = schema({"btrfs_bootstrap_refresh": 30, "backend": backend})
    assert backend.bootstrap_refresh_days == 30
    assert "btrfs_bootstrap_refresh" not in data
    # accepts when not provided
    backend2 = btrfs.BtrfsBackend()
    data = schema({"backend": backend2})
    assert backend2.bootstrap_refresh_days is None
    # rejects non-int
    with pytest.raises(vlp.Invalid):
        schema({"btrfs_bootstrap_refresh": "thirty"})
    # rejects zero
    with pytest.raises(vlp.Invalid):
        schema({"btrfs_bootstrap_refresh": 0})
    # rejects negative
    with pytest.raises(vlp.Invalid):
        schema({"btrfs_bootstrap_refresh": -1})
    # rejects float
    with pytest.raises(vlp.Invalid):
        schema({"btrfs_bootstrap_refresh": 1.5})


# --- bootstrap refresh ---


def _age_btrfs_snapshot(path, old_time):
    """Set mtime on a readonly btrfs snapshot by temporarily making it writable."""
    subprocess.run(["btrfs", "property", "set", str(path), "ro", "false"], check=True)
    os.utime(path, (old_time, old_time))
    subprocess.run(["btrfs", "property", "set", str(path), "ro", "true"], check=True)


def test_btrfs_maybe_refresh_bootstrap(btrfs_fs_generator, random_timeframes_generator):
    backend = btrfs.BtrfsBackend()
    src_dir = btrfs_fs_generator()
    dst_dir = btrfs_fs_generator()
    backup = Backup("test-refresh", backend, src_dir, dst_dir, random_timeframes_generator())
    basename = btrfs._btrfs_bootstrap_snapshot_basename()
    src_bootstrap = src_dir.joinpath(basename)
    dst_bootstrap = dst_dir.joinpath(basename)

    # no bootstrap exists yet — should be a no-op
    btrfs._btrfs_maybe_refresh_bootstrap(backup, 1)
    assert not src_bootstrap.is_dir()
    assert not dst_bootstrap.is_dir()

    # create bootstrap snapshots manually
    btrfs._btrfs_bootstrap_local_to_local(src_dir, dst_dir)
    assert src_bootstrap.is_dir()
    assert dst_bootstrap.is_dir()

    # bootstrap is fresh — should not refresh
    with freeze_time("2020-01-02 12:00"):
        _age_btrfs_snapshot(src_bootstrap, datetime(2020, 1, 2).timestamp())
        original_mtime = src_bootstrap.stat().st_mtime
        btrfs._btrfs_maybe_refresh_bootstrap(backup, 1)
    assert src_bootstrap.is_dir()
    assert dst_bootstrap.is_dir()
    assert src_bootstrap.stat().st_mtime == original_mtime

    # age bootstrap past threshold — should delete both
    with freeze_time("2020-01-05 12:00"):
        _age_btrfs_snapshot(src_bootstrap, datetime(2020, 1, 1).timestamp())
        btrfs._btrfs_maybe_refresh_bootstrap(backup, 1)
    assert not src_bootstrap.is_dir()
    assert not dst_bootstrap.is_dir()

    # recreate and test: src deleted but dst already gone — no error
    btrfs._btrfs_bootstrap_local_to_local(src_dir, dst_dir)
    btrfs._btrfs_delete_subvolumes_local(dst_bootstrap)
    assert not dst_bootstrap.is_dir()
    with freeze_time("2020-01-05 12:00"):
        _age_btrfs_snapshot(src_bootstrap, datetime(2020, 1, 1).timestamp())
        btrfs._btrfs_maybe_refresh_bootstrap(backup, 1)
    assert not src_bootstrap.is_dir()
    assert not dst_bootstrap.is_dir()


def test_bootstrap_refresh_local_to_local(btrfs_fs_generator, random_backup_generator):
    backend = btrfs.BtrfsBackend(bootstrap_refresh_days=1)
    backup = random_backup_generator(backend_type="btrfs", backup_type="local_to_local")
    timeframe = backup.timeframes[0]
    timeframe.keep = 5
    with freeze_time("2020-01-01 12:00"):
        backend.do_backup(backup, timeframe)
    src_bootstrap = backup.src_dir.joinpath(btrfs._btrfs_bootstrap_snapshot_basename())
    dst_bootstrap = backup.dst_dir.joinpath(btrfs._btrfs_bootstrap_snapshot_basename())
    assert src_bootstrap.is_dir()
    assert dst_bootstrap.is_dir()
    _age_btrfs_snapshot(src_bootstrap, datetime(2020, 1, 1).timestamp())
    old_mtime = src_bootstrap.stat().st_mtime
    with freeze_time("2020-01-03 12:00"):
        backend.do_backup(backup, timeframe)
    assert src_bootstrap.is_dir()
    assert dst_bootstrap.is_dir()
    assert src_bootstrap.stat().st_mtime > old_mtime
    # verify follow-up incremental backup works with the refreshed bootstrap
    with freeze_time("2020-01-03 13:00"):
        backend.do_backup(backup, timeframe)
    assert len(bckp.backups_collect(backup, timeframe=timeframe)) == 3


def test_bootstrap_refresh_local_to_remote(
    btrfs_fs_generator, sshtarget_generator, random_backup_generator
):
    backend = btrfs.BtrfsBackend(bootstrap_refresh_days=1)
    backup = random_backup_generator(backend_type="btrfs", backup_type="local_to_remote")
    timeframe = backup.timeframes[0]
    timeframe.keep = 5
    with freeze_time("2020-01-01 12:00"):
        backend.do_backup(backup, timeframe)
    src_bootstrap = backup.src_dir.joinpath(btrfs._btrfs_bootstrap_snapshot_basename())
    dst_bootstrap = backup.dst_dir.path.joinpath(btrfs._btrfs_bootstrap_snapshot_basename())
    assert src_bootstrap.is_dir()
    assert dst_bootstrap.is_dir()
    _age_btrfs_snapshot(src_bootstrap, datetime(2020, 1, 1).timestamp())
    old_mtime = src_bootstrap.stat().st_mtime
    with freeze_time("2020-01-03 12:00"):
        backend.do_backup(backup, timeframe)
    assert src_bootstrap.is_dir()
    assert dst_bootstrap.is_dir()
    assert src_bootstrap.stat().st_mtime > old_mtime
    # verify follow-up incremental backup works with the refreshed bootstrap
    with freeze_time("2020-01-03 13:00"):
        backend.do_backup(backup, timeframe)
    assert len(bckp.backups_collect(backup, timeframe=timeframe)) == 3


def test_bootstrap_refresh_remote_to_local(
    btrfs_fs_generator, sshtarget_generator, random_backup_generator
):
    backend = btrfs.BtrfsBackend(bootstrap_refresh_days=1)
    backup = random_backup_generator(backend_type="btrfs", backup_type="remote_to_local")
    timeframe = backup.timeframes[0]
    timeframe.keep = 5
    with freeze_time("2020-01-01 12:00"):
        backend.do_backup(backup, timeframe)
    src_bootstrap = backup.src_dir.path.joinpath(btrfs._btrfs_bootstrap_snapshot_basename())
    dst_bootstrap = backup.dst_dir.joinpath(btrfs._btrfs_bootstrap_snapshot_basename())
    assert src_bootstrap.is_dir()
    assert dst_bootstrap.is_dir()
    _age_btrfs_snapshot(src_bootstrap, datetime(2020, 1, 1).timestamp())
    old_mtime = src_bootstrap.stat().st_mtime
    with freeze_time("2020-01-03 12:00"):
        backend.do_backup(backup, timeframe)
    assert src_bootstrap.is_dir()
    assert dst_bootstrap.is_dir()
    assert src_bootstrap.stat().st_mtime > old_mtime
    # verify follow-up incremental backup works with the refreshed bootstrap
    with freeze_time("2020-01-03 13:00"):
        backend.do_backup(backup, timeframe)
    assert len(bckp.backups_collect(backup, timeframe=timeframe)) == 3


def test_bootstrap_no_refresh_when_young(btrfs_fs_generator, random_backup_generator):
    backend = btrfs.BtrfsBackend(bootstrap_refresh_days=30)
    backup = random_backup_generator(backend_type="btrfs", backup_type="local_to_local")
    timeframe = backup.timeframes[0]
    timeframe.keep = 5
    with freeze_time("2020-01-01 12:00"):
        backend.do_backup(backup, timeframe)
    src_bootstrap = backup.src_dir.joinpath(btrfs._btrfs_bootstrap_snapshot_basename())
    original_mtime = src_bootstrap.stat().st_mtime
    with freeze_time("2020-01-02 12:00"):
        backend.do_backup(backup, timeframe)
    assert src_bootstrap.stat().st_mtime == original_mtime


def test_bootstrap_no_refresh_at_boundary(btrfs_fs_generator, random_backup_generator):
    backend = btrfs.BtrfsBackend(bootstrap_refresh_days=1)
    backup = random_backup_generator(backend_type="btrfs", backup_type="local_to_local")
    timeframe = backup.timeframes[0]
    timeframe.keep = 5
    with freeze_time("2020-01-01 12:00"):
        backend.do_backup(backup, timeframe)
    src_bootstrap = backup.src_dir.joinpath(btrfs._btrfs_bootstrap_snapshot_basename())
    # age to exactly 1 day (== refresh_days), should NOT refresh
    _age_btrfs_snapshot(src_bootstrap, datetime(2020, 1, 1, 12, 0).timestamp())
    original_mtime = src_bootstrap.stat().st_mtime
    with freeze_time("2020-01-02 12:00"):
        backend.do_backup(backup, timeframe)
    assert src_bootstrap.stat().st_mtime == original_mtime


def test_bootstrap_no_refresh_when_unset(btrfs_fs_generator, random_backup_generator):
    backend = btrfs.BtrfsBackend()
    assert backend.bootstrap_refresh_days is None
    backup = random_backup_generator(backend_type="btrfs", backup_type="local_to_local")
    timeframe = backup.timeframes[0]
    timeframe.keep = 5
    with freeze_time("2020-01-01 12:00"):
        backend.do_backup(backup, timeframe)
    src_bootstrap = backup.src_dir.joinpath(btrfs._btrfs_bootstrap_snapshot_basename())
    _age_btrfs_snapshot(src_bootstrap, datetime(2019, 1, 1).timestamp())
    original_mtime = src_bootstrap.stat().st_mtime
    with freeze_time("2020-05-01 12:00"):
        backend.do_backup(backup, timeframe)
    assert src_bootstrap.stat().st_mtime == original_mtime


def test_bootstrap_refresh_preserves_existing_backups(btrfs_fs_generator, random_backup_generator):
    backend = btrfs.BtrfsBackend(bootstrap_refresh_days=1)
    backup = random_backup_generator(backend_type="btrfs", backup_type="local_to_local")
    timeframe = backup.timeframes[0]
    timeframe.keep = 10
    now = datetime.now()
    for i in range(3):
        with freeze_time(now + timedelta(hours=i)):
            backend.do_backup(backup, timeframe)
    backups_before = bckp.backups_collect(backup, timeframe=timeframe)
    assert len(backups_before) == 3
    src_bootstrap = backup.src_dir.joinpath(btrfs._btrfs_bootstrap_snapshot_basename())
    # set mtime to 2 days before the next frozen time
    next_frozen = now + timedelta(hours=3)
    _age_btrfs_snapshot(src_bootstrap, (next_frozen - timedelta(days=2)).timestamp())
    with freeze_time(next_frozen):
        backend.do_backup(backup, timeframe)
    backups_after = bckp.backups_collect(backup, timeframe=timeframe)
    assert len(backups_after) == 4
    for b in backups_before:
        assert b.is_dir()
