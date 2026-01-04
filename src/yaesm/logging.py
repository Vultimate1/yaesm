# This module is a wrapper over pythons logging module. All logging in yaesm
# should happen through the functions defined in this module.

import logging
import logging.handlers
import inspect

class LoggingNotInitializedException(Exception):
    ...

_logging_initialized = False

def init_logging(stderr=True, logfile=None, syslog=False, syslog_address="/dev/log", level=logging.INFO):
    """Initialize logging for yaesm. Yaesm can log to any and all of stderr,
    syslog, and a file. If this function is called multiple times then it will
    fully re-initialize the logging. If none of 'stderr', 'logfile', or 'syslog'
    are True, then 'stderr' is set to True.

    Note that yaesm supports only two levels of logging, INFO and DEBUG.
    """
    if not (stderr or logfile or syslog):
        stderr = True
    formatter = logging.Formatter("yaesm - %(asctime)s - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S")
    handlers = []
    if syslog:
        syslog_handler = logging.handlers.SysLogHandler(address=syslog_address)
        syslog_handler.setFormatter(formatter)
        syslog_handler.setLevel(level)
        handlers.append(syslog_handler)
    if stderr:
        stderr_handler = logging.StreamHandler() # defaults to sys.stderr
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
    global _logging_initialized
    _logging_initialized = True

def disable_logging():
    """Disable all logging by removing all handlers and marking logging as uninitialized."""
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()
    root_logger.setLevel(logging.CRITICAL + 1)
    global _logging_initialized
    _logging_initialized = False

def logger(name=None):
    """Return a logger with the specified name. If name is None then it defaults
    to the name of the callers module. If logging has not yet been initialized
    (i.e. init_logging() has not been invoked), then raise an exception.
    """
    global _logging_initialized
    if not _logging_initialized:
        raise LoggingNotInitializedException("trying to get logger but logging has not been initialized")
    if name is None:
        name = inspect.getmodule(inspect.stack()[1][0]).__name__
    logger = logging.getLogger(name)
    return logger
