"""Tests for shared path-backend behavior."""

import shutil
from datetime import datetime
from pathlib import Path

import pytest

from yaesm.backend import pathsupport
from yaesm.backend.rsyncbackend import RsyncBackend
from yaesm.backup import BackupArtifact, BackupError
from yaesm.sshtarget import SSHTarget


def test_config_schema(path_generator):
    backend = RsyncBackend()
    src_dir = path_generator("src", mkdir=True)
    dst_dir = path_generator("dst", mkdir=True)

    pathsupport.config_schema()({"backend": backend, "src_dir": src_dir, "dst_dir": dst_dir})

    assert backend.src_dir == src_dir
    assert backend.dst_dir == dst_dir


@pytest.mark.parametrize(
    ("src_remote", "dst_remote", "expected"),
    [
        (False, False, "local_to_local"),
        (False, True, "local_to_remote"),
        (True, False, "remote_to_local"),
        (True, True, "remote_to_remote"),
    ],
)
def test_backup_type(src_remote, dst_remote, expected):
    backend = RsyncBackend()
    target = SSHTarget("ssh://user@example:/", Path("/key"))
    src_dir = target.with_path(Path("/source")) if src_remote else Path("/source")
    dst_dir = target.with_path(Path("/destination")) if dst_remote else Path("/destination")

    pathsupport.configure_paths(backend, src_dir, dst_dir)

    assert pathsupport.backup_type(backend) == expected


def test_configure_paths_rejects_different_remote_endpoints():
    backend = RsyncBackend()
    src_dir = SSHTarget("ssh://user@source:/source", Path("/key"))
    dst_dir = SSHTarget("ssh://user@destination:/destination", Path("/key"))

    with pytest.raises(BackupError, match="must use the same SSH user, host, and port"):
        pathsupport.configure_paths(backend, src_dir, dst_dir)


def test_format_locator():
    backend = RsyncBackend()
    pathsupport.configure_paths(
        backend,
        Path("/source"),
        SSHTarget("ssh://backup@example:/backups", Path("/key")),
    )
    artifact = BackupArtifact("name", "hourly", datetime.now(), "/backups/name")

    assert pathsupport.format_locator(backend, artifact) == "ssh://backup@example:/backups/name"


def test_check_local(monkeypatch, path_generator):
    backend = RsyncBackend()
    pathsupport.configure_paths(
        backend,
        path_generator("src", mkdir=True),
        path_generator("dst", mkdir=True),
    )
    monkeypatch.setattr(shutil, "which", lambda _command: "/usr/bin/rsync")

    assert all(result.passed for result in pathsupport.check(backend))
