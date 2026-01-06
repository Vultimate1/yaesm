import pytest
import logging
import subprocess
import re

from yaesm.logging import logger, init_logging, disable_logging, LoggingNotInitializedException

def test_raises_logging_not_initialized():
    with pytest.raises(LoggingNotInitializedException):
        logger()

def test_init_logging():
    init_logging(stderr=True)
    assert len(logger("").handlers) == 1
    assert logger("").level == logging.INFO

    init_logging(syslog=True, stderr=True, logfile="/var/log/yaesm_test_logging.log", level=logging.DEBUG)
    assert len(logger("").handlers) == 3
    assert logger("").level == logging.DEBUG

    init_logging()
    assert len(logger("").handlers) == 1
    assert logger("").level == logging.INFO

def test_stderr_logging(capsys):
    init_logging(stderr=True, level=logging.DEBUG)
    logger().debug("TEST LOG")
    assert re.match("^yaesm |.+DEBUG.+TEST LOG$", capsys.readouterr().err)

def test_level_respected(capsys):
    init_logging(stderr=True) # level defaults to INFO
    logger().debug("TEST LOG")
    assert "" == capsys.readouterr().err

    logger().error("TEST LOG")
    assert re.match(".+ERROR.+TEST LOG$", capsys.readouterr().err)

def test_logfile_logging(path_generator):
    logfile = path_generator("yaesm_test_logging")
    init_logging(logfile=logfile)
    logger().info("TEST LOG")
    assert logfile.is_file()
    assert re.match(".+INFO.+TEST LOG$", logfile.read_text())

def test_syslog_logging():
    init_logging(syslog=True)
    logger().info("TEST LOG SYSLOG")
    found_log = False
    with open("/var/log/syslog", "r") as syslog:
        for line in syslog:
            if re.match(".+INFO.+TEST LOG SYSLOG", line):
                found_log = True
                break
    assert found_log

def test_multi_dest_logging(capsys, path_generator):
    logfile = path_generator("yaesm_test_logging")
    init_logging(stderr=True, logfile=logfile)
    logger().info("TEST LOG MULTI DEST")
    assert re.match(".+INFO.+TEST LOG MULTI DEST$", capsys.readouterr().err)
    assert re.match(".+INFO.+TEST LOG MULTI DEST$", logfile.read_text())

def test_disable_logging(capsys):
    init_logging(stderr=True)
    disable_logging()
    with pytest.raises(LoggingNotInitializedException):
        logger().info("TEST LOG DISABLED")
    assert "" == capsys.readouterr().err
