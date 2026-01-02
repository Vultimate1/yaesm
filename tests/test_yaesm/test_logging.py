"""tests/test_yaesm/test_logging.py"""

import logging
import re

from yaesm.logging import Logging

def test_init_logging():
    logger = Logging(stderr=True)
    assert len(logger.get("").handlers) == 1
    assert logger.get("").level == logging.INFO

    logger = Logging(syslog=True, stderr=True, logfile="/var/log/yaesm_test_logging.log",
                     level=logging.DEBUG)
    assert len(logger.get("").handlers) == 3
    assert logger.get("").level == logging.DEBUG

    logger = Logging()
    assert len(logger.get("").handlers) == 1
    assert logger.get("").level == logging.INFO

def test_stderr_logging(capsys):
    logger = Logging(stderr=True, level=logging.DEBUG)
    logger.get().debug("TEST LOG")
    assert re.match("^yaesm |.+DEBUG.+TEST LOG$", capsys.readouterr().err)

def test_level_respected(capsys):
    logger = Logging(stderr=True) # level defaults to INFO
    logger.get().debug("TEST LOG")
    assert "" == capsys.readouterr().err

    logger.get().error("TEST LOG")
    assert re.match(".+ERROR.+TEST LOG$", capsys.readouterr().err)

def test_logfile_logging(path_generator):
    logfile = path_generator("yaesm_test_logging")
    logger = Logging(logfile=logfile)
    logger.get().info("TEST LOG")
    assert logfile.is_file()
    assert re.match(".+INFO.+TEST LOG$", logfile.read_text())

def test_syslog_logging():
    logger = Logging(syslog=True)
    logger.get().info("TEST LOG SYSLOG")
    found_log = False
    with open("/var/log/syslog", "r", encoding="utf-8") as syslog:
        for line in syslog:
            if re.match(".+INFO.+TEST LOG SYSLOG", line):
                found_log = True
                break
    assert found_log

def test_multi_dest_logging(capsys, path_generator):
    logfile = path_generator("yaesm_test_logging")
    logger = Logging(stderr=True, logfile=logfile)
    logger.get().info("TEST LOG MULTI DEST")
    assert re.match(".+INFO.+TEST LOG MULTI DEST$", capsys.readouterr().err)
    assert re.match(".+INFO.+TEST LOG MULTI DEST$", logfile.read_text())

def test_disable_logging(capsys):
    init_logging(stderr=True)
    disable_logging()
    with pytest.raises(LoggingNotInitializedException):
        logger().info("TEST LOG DISABLED")
    assert "" == capsys.readouterr().err
