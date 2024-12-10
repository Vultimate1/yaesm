import pytest
import shutil
import subprocess
import os
from pathlib import Path
from datetime import datetime, timedelta
from freezegun import freeze_time

import yaesm.backend.btrfsbackend as btrfs
import yaesm.backup as bckp
from yaesm.sshtarget import SSHTarget

@pytest.fixture(scope="session")
def btrfs_backend():
    return btrfs.BtrfsBackend()

def test_do_backup(btrfs_backend, random_backup_generator, btrfs_fs, btrfs_sudo_access, path_generator):
    for backup_type in ["local_to_local", "local_to_remote,", "remote_to_local"]:
        backup = random_backup_generator(btrfs_fs, backup_type=backup_type, dst_dir_base=btrfs_fs)
        timeframe = backup.timeframes[0]
        timeframe.keep = 3
        now = datetime.now()
        expected_backup_basenames = []
        for i in range(timeframe.keep+2):
            with freeze_time(now + timedelta(hours=i)):
                expected_backup_basenames.insert(0, bckp.backup_basename_now())
                btrfs_backend.do_backup(backup, timeframe)
        if isinstance(backup.dst_dir, SSHTarget):
            dst_dir = backup.dst_dir.path.joinpath(timeframe.name)
        else:
            dst_dir = backup.dst_dir.joinpath(timeframe.name)
        backups = bckp.backups_collect(dst_dir)
        backup_basenames = list(map(lambda x: x.path.name if isinstance(x, SSHTarget) else x.name, backups))
        assert len(backups) == timeframe.keep
        assert expected_backup_basenames[0:timeframe.keep] == backup_basenames

def test_exec_backup_local_to_local(btrfs_backend, btrfs_fs, path_generator):
    dst_dir = path_generator("test-dst-dir", base_dir=btrfs_fs, mkdir=True)
    backup = dst_dir.joinpath(bckp.backup_basename_now())
    assert not backup.is_dir()
    btrfs_backend._exec_backup_local_to_local(btrfs_fs, dst_dir)
    assert backup.is_dir()

def test_exec_backup_local_to_remote(btrfs_backend, sshtarget, btrfs_fs, btrfs_sudo_access, path_generator):
    dst_dir = sshtarget.with_path(path_generator("test-dst-dir", base_dir=btrfs_fs, mkdir=True))
    backup = dst_dir.path.joinpath(bckp.backup_basename_now())
    assert not backup.is_dir()
    btrfs_backend._exec_backup_local_to_remote(btrfs_fs, dst_dir)
    assert backup.is_dir()

def test_exec_backup_remote_to_local(btrfs_backend, sshtarget, btrfs_fs, btrfs_sudo_access, path_generator):
    src_dir = sshtarget.with_path(btrfs_fs)
    dst_dir = path_generator("test-dst-dir", base_dir=btrfs_fs, mkdir=True)
    backup = dst_dir.joinpath(bckp.backup_basename_now())
    assert not backup.is_dir()
    btrfs_backend._exec_backup_remote_to_local(src_dir, dst_dir)
    assert backup.is_dir()

def test_btrfs_take_and_delete_snapshot_local(btrfs_fs, path_generator):
    dst_dir1 = path_generator("test-snapshot", base_dir=btrfs_fs, mkdir=True)
    dst_dir2 = path_generator("test-snapshot", base_dir=btrfs_fs, mkdir=True)
    returncode1, snapshot1 = btrfs._btrfs_take_snapshot_local(btrfs_fs, dst_dir1, add_backup_basename_now=True)
    returncode2, snapshot2 = btrfs._btrfs_take_snapshot_local(btrfs_fs, dst_dir2, add_backup_basename_now=True)
    assert 0 == returncode1
    assert 0 == returncode2
    assert bckp.backup_basename_re().match(snapshot1.name)
    assert bckp.backup_basename_re().match(snapshot2.name)
    assert 1 == len(os.listdir(dst_dir1))
    assert 1 == len(os.listdir(dst_dir2))
    returncode, deleted = btrfs._btrfs_delete_subvolumes_local(snapshot1, snapshot2)
    assert 0 == returncode
    assert [snapshot1, snapshot2] == deleted
    assert 0 == len(os.listdir(dst_dir1))
    assert 0 == len(os.listdir(dst_dir2))
    dst_dir = path_generator("test-snapshot", base_dir=btrfs_fs, mkdir=True).joinpath("foo")
    returncode, snapshot = btrfs._btrfs_take_snapshot_local(btrfs_fs, dst_dir)
    assert 0 == returncode
    assert snapshot.is_dir()
    assert snapshot.name == "foo"
    returncode, deleted = btrfs._btrfs_delete_subvolumes_local(snapshot)
    assert 0 == returncode
    assert [snapshot] == deleted
    assert not snapshot.is_dir()
    assert 0 == len(os.listdir(dst_dir.parent))

def test_btrfs_take_and_delete_snapshot_remote(btrfs_fs, btrfs_sudo_access, sshtarget, path_generator):
    src_dir = sshtarget.with_path(btrfs_fs)
    dst_dir1 = sshtarget.with_path(path_generator("test-snapshot", base_dir=btrfs_fs, mkdir=True))
    dst_dir2 = sshtarget.with_path(path_generator("test-snapshot", base_dir=btrfs_fs, mkdir=True))
    returncode1, snapshot1 = btrfs._btrfs_take_snapshot_remote(src_dir, dst_dir1, add_backup_basename_now=True)
    returncode2, snapshot2 = btrfs._btrfs_take_snapshot_remote(src_dir, dst_dir2, add_backup_basename_now=True)
    assert 0 == returncode1
    assert 0 == returncode2
    assert bckp.backup_basename_re().match(snapshot1.path.name)
    assert bckp.backup_basename_re().match(snapshot2.path.name)
    assert 1 == len(os.listdir(dst_dir1.path))
    assert 1 == len(os.listdir(dst_dir2.path))
    returncode, deleted = btrfs._btrfs_delete_subvolumes_remote(snapshot1, snapshot2)
    assert 0 == returncode
    assert [snapshot1, snapshot2] == deleted
    assert 0 == len(os.listdir(dst_dir1.path))
    assert 0 == len(os.listdir(dst_dir2.path))
    dst_dir = sshtarget.with_path(path_generator("test-snapshot", base_dir=btrfs_fs, mkdir=True).joinpath("foo"))
    returncode, snapshot = btrfs._btrfs_take_snapshot_remote(src_dir, dst_dir)
    assert 0 == returncode
    assert snapshot.path.is_dir()
    assert snapshot.path.name == "foo"
    returncode, deleted = btrfs._btrfs_delete_subvolumes_remote(snapshot)
    assert 0 == returncode
    assert [snapshot] == deleted
    assert not snapshot.path.is_dir()
    assert 0 == len(os.listdir(dst_dir.path.parent))

def test_btrfs_send_receive_local_to_local(btrfs_fs, path_generator):
    _, parent_snapshot = btrfs._btrfs_take_snapshot_local(btrfs_fs, path_generator("test-parent-snapshot", base_dir=btrfs_fs))
    receive_dir = path_generator("test-btrfs-receive-dst", base_dir=btrfs_fs, mkdir=True)
    returncode, received_snapshot = btrfs._btrfs_send_receive_local_to_local(parent_snapshot, receive_dir)
    assert 0 == returncode
    assert received_snapshot.is_dir()
    assert received_snapshot == receive_dir.joinpath(parent_snapshot.name)
    _, tmp_snapshot = btrfs._btrfs_take_snapshot_local(btrfs_fs, path_generator("test-tmp-snapshot", base_dir=btrfs_fs))
    returncode, received_snapshot = btrfs._btrfs_send_receive_local_to_local(tmp_snapshot, receive_dir, parent=parent_snapshot)
    assert 0 == returncode
    assert received_snapshot.is_dir()
    assert received_snapshot == receive_dir.joinpath(tmp_snapshot.name)

def test_btrfs_send_receive_local_to_remote(btrfs_fs, btrfs_sudo_access, sshtarget, path_generator):
    _, parent_snapshot = btrfs._btrfs_take_snapshot_local(btrfs_fs, path_generator("test-parent-snapshot", base_dir=btrfs_fs))
    receive_dir = sshtarget.with_path(path_generator("test-btrfs-receive-dst", base_dir=btrfs_fs, mkdir=True))
    returncode, received_snapshot = btrfs._btrfs_send_receive_local_to_remote(parent_snapshot, receive_dir)
    assert 0 == returncode
    assert received_snapshot.path.is_dir()
    assert received_snapshot.path == receive_dir.path.joinpath(parent_snapshot.name)
    _, tmp_snapshot = btrfs._btrfs_take_snapshot_local(btrfs_fs, path_generator("test-tmp-snapshot", base_dir=btrfs_fs))
    returncode, received_snapshot = btrfs._btrfs_send_receive_local_to_remote(tmp_snapshot, receive_dir, parent=parent_snapshot)
    assert 0 == returncode
    assert received_snapshot.path.is_dir()
    assert received_snapshot.path == receive_dir.path.joinpath(tmp_snapshot.name)

def test_btrfs_send_receive_remote_to_local(btrfs_fs, btrfs_sudo_access, sshtarget, path_generator):
    _, parent_snapshot = btrfs._btrfs_take_snapshot_local(btrfs_fs, path_generator("test-parent-snapshot", base_dir=btrfs_fs))
    parent_snapshot = sshtarget.with_path(parent_snapshot)
    receive_dir = path_generator("test-btrfs-receive-dst", base_dir=btrfs_fs, mkdir=True)
    returncode, received_snapshot = btrfs._btrfs_send_receive_remote_to_local(parent_snapshot, receive_dir)
    assert 0 == returncode
    assert received_snapshot.is_dir()
    assert received_snapshot == receive_dir.joinpath(parent_snapshot.path.name)
    _, tmp_snapshot = btrfs._btrfs_take_snapshot_local(btrfs_fs, path_generator("test-tmp-snapshot", base_dir=btrfs_fs))
    tmp_snapshot = sshtarget.with_path(tmp_snapshot)
    returncode, received_snapshot = btrfs._btrfs_send_receive_remote_to_local(tmp_snapshot, receive_dir, parent=parent_snapshot)
    assert 0 == returncode
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

def test_btrfs_bootstrap_local_to_remote(btrfs_fs, btrfs_sudo_access, sshtarget, path_generator):
    src_dir = btrfs_fs
    dst_dir = sshtarget.with_path(path_generator("test-btrfs-bootstrap-dst", base_dir=btrfs_fs, mkdir=True))
    dst_bootstrap = dst_dir.with_path(dst_dir.path.joinpath(btrfs._btrfs_bootstrap_snapshot_basename()))
    bootstrap_snapshot = btrfs._btrfs_bootstrap_local_to_remote(src_dir, dst_dir)
    assert bootstrap_snapshot == src_dir.joinpath(btrfs._btrfs_bootstrap_snapshot_basename())
    assert bootstrap_snapshot.is_dir()
    assert dst_bootstrap.path.is_dir()
    btrfs._btrfs_delete_subvolumes_local(dst_bootstrap.path)
    assert not dst_bootstrap.path.is_dir()
    bootstrap_snapshot = btrfs._btrfs_bootstrap_local_to_remote(src_dir, dst_dir)
    assert dst_bootstrap.path.is_dir()

def test_btrfs_bootstrap_remote_to_local(btrfs_fs, btrfs_sudo_access, sshtarget, path_generator):
    src_dir = sshtarget.with_path(btrfs_fs)
    dst_dir = path_generator("test-btrfs-bootstrap-dst", base_dir=btrfs_fs, mkdir=True)
    dst_bootstrap = dst_dir.joinpath(btrfs._btrfs_bootstrap_snapshot_basename())
    bootstrap_snapshot = btrfs._btrfs_bootstrap_remote_to_local(src_dir, dst_dir)
    assert bootstrap_snapshot.path == src_dir.path.joinpath(btrfs._btrfs_bootstrap_snapshot_basename())
    assert bootstrap_snapshot.path.is_dir()
    assert dst_bootstrap.is_dir()
    btrfs._btrfs_delete_subvolumes_local(dst_bootstrap)
    assert not dst_bootstrap.is_dir()
    bootstrap_snapshot = btrfs._btrfs_bootstrap_remote_to_local(src_dir, dst_dir)
    assert dst_bootstrap.is_dir()
