import atexit
import signal
import sys

from yaesm.logging import logger

_cleanup_functions = []
_cleanup_initialized = False

def initialize_cleanup():
    """Initialize cleanup system for graceful shutdown.

    Sets up atexit handler and signal handlers (SIGTERM, SIGINT) to ensure
    all registered cleanup functions are called when the program exits.

    Should be called once during program initialization.
    """
    global _cleanup_initialized
    if not _cleanup_initialized:
        atexit.register(_do_cleanup)
        signal.signal(signal.SIGTERM, _do_cleanup)
        signal.signal(signal.SIGINT, _do_cleanup)
        _cleanup_initialized = True

def add_cleanup_function(func):
    """Register a cleanup function to be run at program termination. Note that
    the most recently registered functions get executed first.
    """
    _cleanup_functions.append(func)

def _do_cleanup(*_args):
    """Execute all registered cleanup functions such that the most recently
    registered functions get executed first.
    """
    exit_status = 0
    for func in reversed(_cleanup_functions):
        try:
            func()
        except Exception as e:
            exit_status = 1
            logger().error(f"cleanup function failed: {e}", exc_info=True)
    if _args: # called from signal handler, otherwise called from atexit
        sys.exit(exit_status)
