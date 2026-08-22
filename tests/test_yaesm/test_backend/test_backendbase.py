"""tests/test_yaesm/test_backend/test_backendbase.py.

Unless we can come up with a clean and simple way to generically test the backends,
every backend test script should have their own tests for the abstract methods in the
BackendBase class.
"""

from datetime import datetime
from pathlib import Path

from yaesm.backend.backendbase import BackendBase, CheckResult, PathBackendBase
from yaesm.backend.btrfsbackend import BtrfsBackend
from yaesm.backend.rsyncbackend import RsyncBackend
from yaesm.backup import BackupArtifact
from yaesm.sshtarget import SSHTarget


def test_check_result_passed():
    assert CheckResult("check").passed
    assert not CheckResult("check", ("error",)).passed


def test_path_backend_classes():
    assert issubclass(PathBackendBase, BackendBase)
    assert issubclass(BtrfsBackend, PathBackendBase)
    assert issubclass(RsyncBackend, PathBackendBase)


def test_path_backend_formats_remote_locator():
    backend = RsyncBackend()
    backend.configure_paths(
        Path("/source"),
        SSHTarget("ssh://backup@example:/backups", Path("/key")),
    )
    artifact = BackupArtifact("name", "hourly", datetime.now(), "/backups/name")

    assert backend.format_locator(artifact) == "ssh://backup@example:/backups/name"
