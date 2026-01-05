"""tests/test_yaesm/test_logging.py"""

import logging
import re

import pytest

from yaesm.logging import Logging, LoggingNotInitializedException

def test_raises_logging_not_initialized():
    with pytest.raises(LoggingNotInitializedException):
        Logging.get()

def test_init_logging():
    Logging.initialize(stderr=True)
    assert len(Logging.get("").handlers) == 1
    assert Logging.get("").level == logging.INFO

    Logging.initialize(syslog=True, stderr=True, logfile="/var/log/yaesm_test_logging.log",
                     level=logging.DEBUG)
    assert len(Logging.get("").handlers) == 3
    assert Logging.get("").level == logging.DEBUG

    Logging.initialize()
    assert len(Logging.get("").handlers) == 1
    assert Logging.get("").level == logging.INFO

def test_stderr_logging(capsys):
    Logging.initialize(stderr=True, level=logging.DEBUG)
    Logging.get().debug("TEST LOG")
    assert re.match(".+DEBUG.+TEST LOG$", capsys.readouterr().err)

def test_level_respected(capsys):
    Logging.initialize(stderr=True) # level defaults to INFO
    Logging.get().debug("TEST LOG")
    assert "" == capsys.readouterr().err

    Logging.get().error("TEST LOG")
    assert re.match(".+ERROR.+TEST LOG$", capsys.readouterr().err)

def test_logfile_logging(path_generator):
    logfile = path_generator("yaesm_test_logging")
    Logging.initialize(logfile=logfile)
    Logging.get().info("TEST LOG")
    assert logfile.is_file()
    assert re.match(".+INFO.+TEST LOG$", logfile.read_text())

def test_syslog_logging():
    Logging.initialize(syslog=True)
    Logging.get().info("TEST LOG SYSLOG")
    found_log = False
    with open("/var/log/syslog", "r", encoding="utf-8") as syslog:
        for line in syslog:
            if re.match(".+INFO.+TEST LOG SYSLOG", line):
                found_log = True
                break
    assert found_log

def test_multi_dest_logging(capsys, path_generator):
    logfile = path_generator("yaesm_test_logging")
    Logging.initialize(stderr=True, logfile=logfile)
    Logging.get().info("TEST LOG MULTI DEST")
    assert re.match(".+INFO.+TEST LOG MULTI DEST$", capsys.readouterr().err)
    assert re.match(".+INFO.+TEST LOG MULTI DEST$", logfile.read_text())

def test_disable_logging(capsys):
    Logging.initialize(stderr=True)
    Logging.disable()
    with pytest.raises(LoggingNotInitializedException):
        Logging.get().info("TEST LOG DISABLED")
    assert "" == capsys.readouterr().err
