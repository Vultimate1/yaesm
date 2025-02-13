import pytest
from pathlib import Path

import yaesm.backend.zfsbackend as zfs
from yaesm.sshtarget import SSHTarget

def test_zfs_snapshot_dir(zfs_fs):
    with pytest.raises(Exception):
        zfs._zfs_snapshot_dir("NOT A REAL ZFS DATASET")
    snapshot_dir = zfs_fs[2]
    dataset_name = zfs_fs[1]
    assert snapshot_dir == zfs._zfs_snapshot_dir(dataset_name)

def test_zfs_take_snapshot_local(zfs_fs):
    zfs_dir = zfs_fs[0]
    dataset_name = zfs_fs[1]
    snapshot_dir = zfs_fs[2]
    zfs._zfs_take_snapshot_local(dataset_name, "foobar_snapshot")
    assert snapshot_dir.joinpath("foobar_snapshot").is_dir()
    returncode, _ = zfs._zfs_take_snapshot_local(zfs_fs[1], "foobar_snapshot", check=False)
    assert 0 != returncode
    zfs_dir.joinpath("foo.txt").touch()
    zfs._zfs_take_snapshot_local(dataset_name, "foobar_snapshot2")
    assert snapshot_dir.joinpath("foobar_snapshot2", "foo.txt").is_file()

def test_zfs_take_snapshot_remote(zfs_fs, sshtarget, zfs_sudo_access):
    dataset_name = sshtarget.with_path(zfs_fs[1])
    zfs._zfs_take_snapshot_remote(dataset_name, "foobar_snapshot")
    snapshot_dir = zfs_fs[2]
    assert snapshot_dir.joinpath("foobar_snapshot").is_dir()
    returncode, snapshot_name = zfs._zfs_take_snapshot_remote(dataset_name, "foobar_snapshot", check=False)
    assert 0 != returncode
    assert isinstance(snapshot_name, SSHTarget)
    Path(zfs_fs[0]).joinpath("foo.txt").touch()
    zfs._zfs_take_snapshot_remote(dataset_name, "foobar_snapshot2")
    assert snapshot_dir.joinpath("foobar_snapshot2", "foo.txt").is_file()
