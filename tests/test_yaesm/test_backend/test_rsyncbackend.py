import pytest

import yaesm.backend.rsyncbackend as rsync
import yaesm.backup as bckp

def test_rsync_do_bootstrap_local(path_generator):
    src_dir = path_generator("src_dir_bootstrap", mkdir=True)
    dst_dir = path_generator("dst_dir_bootstrap", mkdir=True)
    src_dir.joinpath("file.txt").touch()
    assert not rsync._rsync_bootstrap_snapshot_local(dst_dir)
    returncode, bootstrap_snapshot = rsync._rsync_do_bootstrap_local(src_dir, dst_dir)
    assert returncode == 0
    assert bootstrap_snapshot == rsync._rsync_bootstrap_snapshot_local(dst_dir)
    assert bootstrap_snapshot.is_dir()
    assert bootstrap_snapshot.joinpath("file.txt").is_file()
