"""tests/test_yaesm/test_cleanup.py"""

import signal
from unittest.mock import Mock

import pytest

from yaesm.cleanup import Cleanup

@pytest.fixture(autouse=True)
def reset_cleanup_state():
    """Reset cleanup module state between tests."""
    Cleanup._functions = []
    Cleanup._initialized = False
    yield
    Cleanup._functions = []
    Cleanup._initialized = False

def test_add_cleanup_function():
    func1 = Mock()
    func2 = Mock()
    Cleanup.add_function(func1)
    Cleanup.add_function(func2)
    assert len(Cleanup._functions) == 2
    assert Cleanup._functions[0] is func1
    assert Cleanup._functions[1] is func2

def test_cleanup_functions_execute_in_reverse_order():
    call_order = []
    Cleanup.add_function(lambda: call_order.append(1))
    Cleanup.add_function(lambda: call_order.append(2))
    Cleanup.add_function(lambda: call_order.append(3))
    Cleanup._do_cleanup()
    assert call_order == [3, 2, 1]

def test_initialize_cleanup_only_once():
    assert Cleanup._initialized is False
    Cleanup.initialize()
    assert Cleanup._initialized is True
    Cleanup.initialize()
    assert Cleanup._initialized is True

def test_cleanup_continues_after_exception():
    func1 = Mock()
    func2 = Mock(side_effect=RuntimeError("error"))
    func3 = Mock()
    Cleanup.add_function(func1)
    Cleanup.add_function(func2)
    Cleanup.add_function(func3)
    Cleanup._do_cleanup()
    func3.assert_called_once()
    func2.assert_called_once()
    func1.assert_called_once()

def test_cleanup_with_no_functions():
    # Should not crash
    Cleanup._do_cleanup()

def test_multiple_cleanup_functions():
    call_log = []
    def success1():
        call_log.append("success1")
    def failure():
        call_log.append("failure")
        raise ValueError("failed")
    def success2():
        call_log.append("success2")
    Cleanup.add_function(success1)
    Cleanup.add_function(failure)
    Cleanup.add_function(success2)
    Cleanup._do_cleanup()
    assert call_log == ["success2", "failure", "success1"]

def test_real_world_usage():
    resource1_closed = False
    resource2_closed = False
    def close_resource1():
        nonlocal resource1_closed
        resource1_closed = True
    def close_resource2():
        nonlocal resource2_closed
        resource2_closed = True
    Cleanup.initialize()
    Cleanup.add_function(close_resource1)
    Cleanup.add_function(close_resource2)
    Cleanup._do_cleanup()
    assert resource1_closed
    assert resource2_closed

def test_signal_handlers_registered():
    original_sigterm = signal.getsignal(signal.SIGTERM)
    original_sigint = signal.getsignal(signal.SIGINT)
    Cleanup.initialize()
    new_sigterm = signal.getsignal(signal.SIGTERM)
    new_sigint = signal.getsignal(signal.SIGINT)
    assert new_sigterm is Cleanup._do_cleanup
    assert new_sigint is Cleanup._do_cleanup
    signal.signal(signal.SIGTERM, original_sigterm)
    signal.signal(signal.SIGINT, original_sigint)
