"""tests/test_yaesm/test_logging.py."""

import logging
import re
import subprocess
import time
import uuid
from pathlib import Path
from unittest import mock

import pytest

from yaesm.logging import configure

log = logging.getLogger(__name__)


def test_configure_handlers_and_level():
    configure(stderr=True)
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert root.level == logging.INFO

    configure(
        syslog=True, stderr=True, logfile="/var/log/yaesm_test_logging.log", level=logging.DEBUG
    )
    root = logging.getLogger()
    assert len(root.handlers) == 3
    assert root.level == logging.DEBUG

    configure()
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert root.level == logging.INFO


def test_configure_closes_replaced_handlers():
    configure(stderr=True)
    old_handler = logging.getLogger().handlers[0]

    with mock.patch.object(old_handler, "close", wraps=old_handler.close) as close:
        configure(stderr=True)

    close.assert_called_once_with()


def test_stderr_logging(capsys):
    configure(stderr=True, level=logging.DEBUG)
    log.debug("TEST LOG")
    assert re.match(".+DEBUG.+TEST LOG$", capsys.readouterr().err)


def test_level_respected(capsys):
    configure(stderr=True)  # level defaults to INFO
    log.debug("TEST LOG")
    assert capsys.readouterr().err == ""

    log.error("TEST LOG")
    assert re.match(".+ERROR.+TEST LOG$", capsys.readouterr().err)


def test_logfile_logging(path_generator):
    logfile = path_generator("yaesm_test_logging")
    configure(logfile=logfile)
    log.info("TEST LOG")
    assert logfile.is_file()
    assert re.match(".+INFO.+TEST LOG$", logfile.read_text())


def test_syslog_logging():
    marker = f"YAESM-TEST-{uuid.uuid4().hex}"
    configure(syslog=True)
    log.info(marker)

    syslog = Path("/var/log/syslog")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if marker in syslog.read_text(encoding="utf-8"):
            return
        time.sleep(0.05)

    pytest.fail(f"syslog message did not appear within 2 seconds: {marker}")


def test_multi_dest_logging(capsys, path_generator):
    logfile = path_generator("yaesm_test_logging")
    configure(stderr=True, logfile=logfile)
    log.info("TEST LOG MULTI DEST")
    assert re.match(".+INFO.+TEST LOG MULTI DEST$", capsys.readouterr().err)
    assert re.match(".+INFO.+TEST LOG MULTI DEST$", logfile.read_text())


def test_subprocess_commands_logged_at_debug(capsys):
    configure(stderr=True, level=logging.DEBUG)
    subprocess.run(["echo", "yaesm-audit-hook-marker"], capture_output=True, check=True)
    err = capsys.readouterr().err
    assert "DEBUG" in err
    assert "yaesm-audit-hook-marker" in err


def test_subprocess_commands_not_logged_below_debug(capsys):
    configure(stderr=True, level=logging.INFO)
    subprocess.run(["echo", "yaesm-audit-hook-marker"], capture_output=True, check=True)
    assert "yaesm-audit-hook-marker" not in capsys.readouterr().err
