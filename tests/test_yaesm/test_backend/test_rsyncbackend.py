"""tests/test_yaesm/test_backend/test_rsyncbackend.py."""

import filecmp
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import voluptuous as vlp
from freezegun import freeze_time

import yaesm.backend.rsyncbackend as rsync
import yaesm.backup as bckp
from yaesm.backup import Backup
from yaesm.sshtarget import SSHTarget


@pytest.fixture(scope="session")
def rsync_backend():
    return rsync.RsyncBackend()


def test_config_schema():
    schema = rsync.RsyncBackend.config_schema()
    d = {"rsync_extra_opts": "--exclude /mnt/* --size-only"}
    assert schema(d) == {"extra_opts": ["--exclude", "/mnt/*", "--size-only"]}
    d = {"rsync_extra_opts": ["--exclude /mnt/*", "--size-only"]}
    assert schema(d) == {"extra_opts": ["--exclude", "/mnt/*", "--size-only"]}
    d = {"rsync_extra_opts": ["--exclude", "/mnt/*", "--size-only"]}
    assert schema(d) == {"extra_opts": ["--exclude", "/mnt/*", "--size-only"]}
    d = {"rsync_extra_opts": ["--exclude", "/mnt/*", "--size-only"], "extra_opt": "foo"}
    assert schema(d) == {"extra_opts": ["--exclude", "/mnt/*", "--size-only"], "extra_opt": "foo"}
    d = {"random_opt1": "foo", "random_opt2": "bar"}
    assert schema(d) == {"random_opt1": "foo", "random_opt2": "bar"}
    d = {}
    assert schema(d) == {}
    with pytest.raises(vlp.Invalid) as exc:
        d = {"rsync_extra_opts": 12}
        schema(d)
    assert str(exc.value).startswith("expected str for dictionary value @")


def test_exec_backup(
    rsync_backend, path_generator, random_backup_generator, random_filesystem_modifier
):
    src_dir = path_generator("rsync_src_dir", mkdir=True)
    for backup_type in ["local_to_local", "local_to_remote,", "remote_to_local"]:
        backup = random_backup_generator(backend_type="rsync", backup_type=backup_type)
        timeframe = backup.timeframes[0]
        src_dir = backup.src_dir.path if isinstance(backup.src_dir, SSHTarget) else backup.src_dir
        if backup_type == "local_to_remote":
            src_dir.chmod(0o777)
        now = datetime.now()
        assert len(bckp.backups_collect(backup, timeframe)) == 0
        backups = []
        for i in range(5):
            new_files, deleted_files, modified_files = random_filesystem_modifier(src_dir)
            with freeze_time(now + timedelta(hours=i)):
                backups.insert(
                    0,
                    rsync_backend._exec_backup(
                        backup, bckp.backup_basename_now(backup, timeframe), timeframe
                    ),
                )
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
        assert len(backups) == 5
        if backup_type == "local_to_remote":
            assert all(isinstance(x, SSHTarget) for x in backups)
        else:
            assert all(isinstance(x, Path) for x in backups)


def test_delete_backups_local(rsync_backend, path_generator):
    dst_dir = path_generator("rsync_test_dst_dir", mkdir=True)
    backups = []
    for i in range(5):
        backup = dst_dir.joinpath(f"yaesm-test-backup-5minute.1999_05_13_1999_0{i}:30")
        backup.mkdir()
        backups.append(backup)
    assert all(x.is_dir() for x in backups)
    rsync_backend._delete_backups_local(*backups)
    assert all(not (x.is_dir()) for x in backups)


def test_delete_backups_remote(rsync_backend, sshtarget, path_generator):
    dst_dir = path_generator("rsync_test_dst_dir", mkdir=True)
    backups = []
    for i in range(6):
        backup = dst_dir.joinpath(f"yaesm-test-backup-5minute.1999_05_13_1999_0{i}:30")
        backup.mkdir()
        backups.append(sshtarget.with_path(backup))
    saved_backup = backups[0]
    backups = backups[1:]
    assert all(x.is_dir() for x in backups)
    rsync_backend._delete_backups_remote(*backups)
    assert all(not (x.is_dir()) for x in backups)
    assert saved_backup.is_dir()


# --- check: local_to_local ---


def test_check_local_to_local_pass(rsync_backend, path_generator, random_timeframes_generator):
    src_dir = path_generator("rsync-src", mkdir=True)
    dst_dir = path_generator("rsync-dst", mkdir=True)
    backup = Backup("test", rsync_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = rsync_backend.check(backup)
    assert errors == []


def test_check_local_to_local_src_dir_missing(
    rsync_backend, path_generator, random_timeframes_generator
):
    src_dir = path_generator("nonexistent-src")
    dst_dir = path_generator("rsync-dst", mkdir=True)
    backup = Backup("test", rsync_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = rsync_backend.check(backup)
    assert any("src_dir" in e and "does not exist" in e for e in errors)


def test_check_local_to_local_dst_dir_missing(
    rsync_backend, path_generator, random_timeframes_generator
):
    src_dir = path_generator("rsync-src", mkdir=True)
    dst_dir = path_generator("nonexistent-dst")
    backup = Backup("test", rsync_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = rsync_backend.check(backup)
    assert any("dst_dir" in e and "does not exist" in e for e in errors)


def test_check_local_to_local_tool_missing(
    monkeypatch, rsync_backend, path_generator, random_timeframes_generator
):
    src_dir = path_generator("rsync-src", mkdir=True)
    dst_dir = path_generator("rsync-dst", mkdir=True)
    backup = Backup("test", rsync_backend, src_dir, dst_dir, random_timeframes_generator())
    monkeypatch.setattr(shutil, "which", lambda _tool: None)
    errors = rsync_backend.check(backup)
    assert any("not found locally" in e and "rsync" in e for e in errors)


# --- check: local_to_remote ---


def test_check_local_to_remote_pass(
    rsync_backend, path_generator, sshtarget_generator, random_timeframes_generator
):
    src_dir = path_generator("rsync-src", mkdir=True)
    dst_dir_path = path_generator("rsync-dst", mkdir=True)
    sshtarget = sshtarget_generator()
    dst_dir = sshtarget.with_path(dst_dir_path)
    backup = Backup("test", rsync_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = rsync_backend.check(backup)
    assert errors == []


def test_check_local_to_remote_ssh_fail(
    rsync_backend, path_generator, sshtarget_generator, random_timeframes_generator
):
    src_dir = path_generator("rsync-src", mkdir=True)
    dst_dir_path = path_generator("rsync-dst", mkdir=True)
    sshtarget = sshtarget_generator()
    dst_dir = sshtarget.with_path(dst_dir_path)
    dst_dir.key = path_generator("bad-key", touch=True)
    dst_dir.user = "nonexistent-user"
    backup = Backup("test", rsync_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = rsync_backend.check(backup)
    assert any("SSH" in e or "ssh" in e or "cannot" in e.lower() for e in errors)


def test_check_local_to_remote_remote_dir_missing(
    rsync_backend, path_generator, sshtarget_generator, random_timeframes_generator
):
    src_dir = path_generator("rsync-src", mkdir=True)
    sshtarget = sshtarget_generator()
    dst_dir = sshtarget.with_path(path_generator("nonexistent-remote-dst"))
    backup = Backup("test", rsync_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = rsync_backend.check(backup)
    assert any("dst_dir" in e and "does not exist" in e for e in errors)


def test_check_local_to_remote_remote_tool_missing(
    monkeypatch, rsync_backend, path_generator, sshtarget_generator, random_timeframes_generator
):
    src_dir = path_generator("rsync-src", mkdir=True)
    dst_dir_path = path_generator("rsync-dst", mkdir=True)
    sshtarget = sshtarget_generator()
    dst_dir = sshtarget.with_path(dst_dir_path)
    backup = Backup("test", rsync_backend, src_dir, dst_dir, random_timeframes_generator())
    original_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and "type rsync" in " ".join(str(c) for c in cmd):
            return subprocess.CompletedProcess(cmd, returncode=1)
        return original_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    errors = rsync_backend.check(backup)
    assert any("not found on remote" in e and "rsync" in e for e in errors)


# --- check: remote_to_local ---


def test_check_remote_to_local_pass(
    rsync_backend, path_generator, sshtarget_generator, random_timeframes_generator
):
    src_dir_path = path_generator("rsync-src", mkdir=True)
    dst_dir = path_generator("rsync-dst", mkdir=True)
    sshtarget = sshtarget_generator()
    src_dir = sshtarget.with_path(src_dir_path)
    backup = Backup("test", rsync_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = rsync_backend.check(backup)
    assert errors == []


def test_check_remote_to_local_ssh_fail(
    rsync_backend, path_generator, sshtarget_generator, random_timeframes_generator
):
    src_dir_path = path_generator("rsync-src", mkdir=True)
    dst_dir = path_generator("rsync-dst", mkdir=True)
    sshtarget = sshtarget_generator()
    src_dir = sshtarget.with_path(src_dir_path)
    src_dir.key = path_generator("bad-key", touch=True)
    src_dir.user = "nonexistent-user"
    backup = Backup("test", rsync_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = rsync_backend.check(backup)
    assert any("SSH" in e or "ssh" in e or "cannot" in e.lower() for e in errors)


def test_check_remote_to_local_remote_dir_missing(
    rsync_backend, path_generator, sshtarget_generator, random_timeframes_generator
):
    sshtarget = sshtarget_generator()
    src_dir = sshtarget.with_path(path_generator("nonexistent-remote-src"))
    dst_dir = path_generator("rsync-dst", mkdir=True)
    backup = Backup("test", rsync_backend, src_dir, dst_dir, random_timeframes_generator())
    errors = rsync_backend.check(backup)
    assert any("src_dir" in e and "does not exist" in e for e in errors)


def test_check_remote_to_local_remote_tool_missing(
    monkeypatch, rsync_backend, path_generator, sshtarget_generator, random_timeframes_generator
):
    src_dir_path = path_generator("rsync-src", mkdir=True)
    dst_dir = path_generator("rsync-dst", mkdir=True)
    sshtarget = sshtarget_generator()
    src_dir = sshtarget.with_path(src_dir_path)
    backup = Backup("test", rsync_backend, src_dir, dst_dir, random_timeframes_generator())
    original_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and "type rsync" in " ".join(str(c) for c in cmd):
            return subprocess.CompletedProcess(cmd, returncode=1)
        return original_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    errors = rsync_backend.check(backup)
    assert any("not found on remote" in e and "rsync" in e for e in errors)


def test_check_local_to_remote_remote_dst_not_writable(
    monkeypatch, rsync_backend, path_generator, sshtarget_generator, random_timeframes_generator
):
    src_dir = path_generator("rsync-src", mkdir=True)
    dst_dir_path = path_generator("rsync-dst", mkdir=True)
    sshtarget = sshtarget_generator()
    dst_dir = sshtarget.with_path(dst_dir_path)
    backup = Backup("test", rsync_backend, src_dir, dst_dir, random_timeframes_generator())
    original_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and "test -w" in " ".join(str(c) for c in cmd):
            return subprocess.CompletedProcess(cmd, returncode=1)
        return original_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    errors = rsync_backend.check(backup)
    assert any("dst_dir" in e and "not writable" in e for e in errors)


def test_check_remote_to_local_remote_src_not_readable(
    monkeypatch, rsync_backend, path_generator, sshtarget_generator, random_timeframes_generator
):
    src_dir_path = path_generator("rsync-src", mkdir=True)
    dst_dir = path_generator("rsync-dst", mkdir=True)
    sshtarget = sshtarget_generator()
    src_dir = sshtarget.with_path(src_dir_path)
    backup = Backup("test", rsync_backend, src_dir, dst_dir, random_timeframes_generator())
    original_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and "test -r" in " ".join(str(c) for c in cmd):
            return subprocess.CompletedProcess(cmd, returncode=1)
        return original_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    errors = rsync_backend.check(backup)
    assert any("src_dir" in e and "not readable" in e for e in errors)
