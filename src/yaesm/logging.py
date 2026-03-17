"""src/yaesm/logging.py.

This module is a wrapper over Python's logging module. All logging in yaesm
should happen through the functions defined in this module.
"""

import inspect
import logging
import logging.handlers
from pathlib import Path

import yaesm.ty as ty


class LoggingNotInitializedException(Exception): ...


class Logging:
    _logging_initialized = False

    @staticmethod
    def initialize(
        stderr: bool = False,
        logfile: Path | str | None = None,
        syslog: bool = False,
        syslog_address: str = "/dev/log",
        level: int | str = logging.INFO,
    ) -> None:
        """Initialize logging for yaesm. Yaesm can log to any and all of stderr,
        syslog, and a file. If this function is called multiple times then it will
        fully re-initialize the logging. If none of `stderr`, `logfile`, or `syslog`
        are True, then `stderr` is set to True.

        Note that yaesm supports only two levels of logging, INFO and DEBUG.
        """
        if not (stderr or logfile or syslog):
            stderr = True
        formatter = logging.Formatter(
            "yaesm - %(asctime)s - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S"
        )
        handlers = []
        if syslog:
            syslog_handler = logging.handlers.SysLogHandler(address=syslog_address)
            syslog_handler.setFormatter(formatter)
            syslog_handler.setLevel(level)
            handlers.append(syslog_handler)
        if stderr:
            stderr_handler = logging.StreamHandler()  # defaults to sys.stderr
            stderr_handler.setFormatter(formatter)
            stderr_handler.setLevel(level)
            handlers.append(stderr_handler)
        if logfile:
            logfile_handler = logging.FileHandler(logfile, encoding="utf-8")
            logfile_handler.setFormatter(formatter)
            logfile_handler.setLevel(level)
            handlers.append(logfile_handler)
        root_logger = logging.getLogger()
        root_logger.setLevel(level)
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        for handler in handlers:
            root_logger.addHandler(handler)
        Logging._logging_initialized = True

    @staticmethod
    def get(name: str | None = None) -> ty.Logger:
        """Return a logger with the specified name. Defaults to the name of the callers
        module. If logging has not yet been initialized (i.e. init_logging() has not
        been invoked), then raise an exception.
        """
        if not Logging._logging_initialized:
            raise LoggingNotInitializedException(
                "trying to get logger but logging has not been initialized"
            )
        if name is None:
            module = inspect.getmodule(inspect.stack()[1][0])
            name = module.__name__ if module is not None else "__main__"
        return logging.getLogger(name)

    @staticmethod
    def disable() -> None:
        """Disable all logging by removing all handlers and marking logging as uninitialized."""
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
            handler.close()
        root_logger.setLevel(logging.CRITICAL + 1)
        Logging._logging_initialized = False
