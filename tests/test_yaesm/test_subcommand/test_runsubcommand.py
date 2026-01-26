import pytest
import argparse
import os
import fcntl
import errno
import logging
from pathlib import Path
from unittest.mock import MagicMock

from yaesm.subcommand.runsubcommand import RunSubcommand
import yaesm.cleanup
import yaesm.scheduler

@pytest.fixture
def runsubcommand():
    return RunSubcommand()

def test_add_argparser_arguments():
    parser = argparse.ArgumentParser()
    RunSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args([])
    assert args.lockfile == Path("/run/lock/yaesm-run.lock")

def test_acquire_scheduler_lock(monkeypatch, runsubcommand, caplog, path_generator):
    parser = argparse.ArgumentParser()
    RunSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args(["--lockfile", str(path_generator("scheduler.lock"))])
    def fail(*_a, **_kw):
        raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
    monkeypatch.setattr(fcntl, "lockf", fail)
    assert 1 == runsubcommand.main([], args)
    assert "could not acquire scheduler lock:" in caplog.text

@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit()])
def test_start_keyboardinterrupt_or_systemexit_returns_0_and_logs(monkeypatch, tmp_path, caplog, exc):
    caplog.set_level(logging.INFO)

    monkeypatch.setattr(yaesm.cleanup, "add_cleanup_function", lambda _fn: None)

    sched = MagicMock()
    sched.start.side_effect = exc
    monkeypatch.setattr(yaesm.scheduler, "Scheduler", lambda: sched)

    parser = argparse.ArgumentParser()
    RunSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args(["--lockfile", str(tmp_path / "scheduler.lock")])

    subcmd = RunSubcommand()
    rc = subcmd.main([], args)

    assert rc == 0
    assert "scheduler stopped gracefully" in caplog.text

    os.close(subcmd._lock_fd)

def test_start_generic_exception_returns_1_and_logs_crashed(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.ERROR)

    monkeypatch.setattr(yaesm.cleanup, "add_cleanup_function", lambda _fn: None)

    sched = MagicMock()
    sched.start.side_effect = RuntimeError("boom")
    monkeypatch.setattr(yaesm.scheduler, "Scheduler", lambda: sched)

    parser = argparse.ArgumentParser()
    RunSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args(["--lockfile", str(tmp_path / "scheduler.lock")])

    subcmd = RunSubcommand()
    rc = subcmd.main([], args)

    assert rc == 1
    assert "scheduler crashed" in caplog.text

    os.close(subcmd._lock_fd)
