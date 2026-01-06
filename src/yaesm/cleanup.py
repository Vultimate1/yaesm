"""src/yaesm/cleanup.py"""

import atexit
import signal
import sys

from yaesm.logging import Logging

class Cleanup:
    _functions = []
    _initialized = False

    @staticmethod
    def initialize():
        """Initialize cleanup system for graceful shutdown.

        Sets up atexit handler and signal handlers (SIGTERM, SIGINT) to ensure
        all registered cleanup functions are called when the program exits.

        Should be called once during program initialization.
        """
        if not Cleanup._initialized:
            atexit.register(Cleanup._do_cleanup)
            signal.signal(signal.SIGTERM, Cleanup._do_cleanup)
            signal.signal(signal.SIGINT, Cleanup._do_cleanup)
            Cleanup._initialized = True

    @staticmethod
    def add_function(func):
        """Register a cleanup function to be run at program termination. Note that
        the most recently registered functions get executed first.
        """
        Cleanup._functions.append(func)

    @staticmethod
    def _do_cleanup(*_args):
        """Execute all registered cleanup functions such that the most recently
        registered functions get executed first.
        """
        exit_status = 0
        for func in reversed(Cleanup._functions):
            try:
                func()
            except Exception as e:
                exit_status = 1
                Logging.get().error("cleanup function failed: %s", e, exc_info=True)
        if _args: # called from signal handler, otherwise called from atexit
            sys.exit(exit_status)
