"""tests/test_yaesm/test_backend/test_backendbase.py.

Unless we can come up with a clean and simple way to generically test the backends,
every backend test script should have their own tests for the abstract methods in the
BackendBase class.
"""

from yaesm.backend.backendbase import BackendBase, CheckResult
from yaesm.backend.btrfsbackend import BtrfsBackend
from yaesm.backend.rsyncbackend import RsyncBackend


def test_check_result_passed():
    assert CheckResult("check").passed
    assert not CheckResult("check", ("error",)).passed


def test_backend_classes_share_one_contract():
    assert BtrfsBackend.__bases__ == (BackendBase,)
    assert RsyncBackend.__bases__ == (BackendBase,)
