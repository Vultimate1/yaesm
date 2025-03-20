import pytest
from datetime import datetime, timedelta
from freezegun import freeze_time
from pathlib import Path
import filecmp

import yaesm.backend.rsyncbackend as rsync
import yaesm.backup as bckp
from yaesm.sshtarget import SSHTarget

@pytest.fixture(scope="session")
def rsync_backend():
    return rsync.RsyncBackend()

def test_exec_backup(rsync_backend, path_generator, random_backup_generator, rsync_sudo_access, random_filesystem_modifier):
    src_dir = path_generator("rsync_src_dir", mkdir=True)
    for backup_type in ["local_to_local", "local_to_remote,", "remote_to_local"]:
        backup = random_backup_generator(src_dir, backup_type=backup_type, dst_dir_base="/tmp")
        timeframe = backup.timeframes[0]
        if isinstance(backup.src_dir, SSHTarget):
            src_dir = backup.src_dir.path
        else:
            src_dir = backup.src_dir
        now = datetime.now()
        assert 0 == len(bckp.backups_collect(backup, timeframe))
        backups = []
        for i in range(5):
            new_files, deleted_files, modified_files = random_filesystem_modifier(src_dir)
            with freeze_time(now + timedelta(hours=i)):
                backups.insert(0, rsync_backend._exec_backup(backup, timeframe))
            if i >= 1:
                new_backup = backups[0]
                prev_backup = backups[1]
                if isinstance(new_backup, SSHTarget):
                    new_backup = new_backup.path
                if isinstance(prev_backup, SSHTarget):
                    prev_backup = prev_backup.path
                for f in new_files:
                    src_f = new_backup.joinpath(*list(f.parts)[1:])
                    new_f = new_backup.joinpath(*list(f.parts)[1:])
                    prev_f = prev_backup.joinpath(*list(f.parts)[1:])
                    assert src_f.is_file()
                    assert new_f.is_file()
                    assert not prev_f.is_file()
                for f in deleted_files:
                    src_f = new_backup.joinpath(*list(f.parts)[1:])
                    new_f = new_backup.joinpath(*list(f.parts)[1:])
                    prev_f = prev_backup.joinpath(*list(f.parts)[1:])
                    assert not src_f.is_file()
                    assert not new_f.is_file()
                    assert prev_f.is_file()
                for f in modified_files:
                    new_f = new_backup.joinpath(*list(f.parts)[1:])
                    prev_f = prev_backup.joinpath(*list(f.parts)[1:])
                    assert new_f.is_file()
                    assert prev_f.is_file()
                    assert not filecmp.cmp(new_f, prev_f, shallow=False)
        assert 5 == len(backups)
        if backup_type == "local_to_remote":
            assert all(isinstance(x, SSHTarget) for x in backups)
        else:
            assert all(isinstance(x, Path) for x in backups)
