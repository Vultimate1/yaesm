import errno
import fcntl
import logging
import os
import signal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import yaesm.cleanup
import yaesm.config
import yaesm.scheduler
from yaesm.subcommand.runsubcommand import RunSubcommand


@pytest.fixture
def runsubcommand():
    return RunSubcommand()


def test_add_argparser_arguments():
    import argparse

    parser = argparse.ArgumentParser()
    RunSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args([])
    assert args.lockfile == Path("/run/lock/yaesm-run.lock")


def test_acquire_scheduler_lock(monkeypatch, runsubcommand, caplog, path_generator):
    import argparse

    parser = argparse.ArgumentParser()
    RunSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args(["--lockfile", str(path_generator("scheduler.lock"))])

    def fail(*_a, **_kw):
        raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")

    monkeypatch.setattr(fcntl, "lockf", fail)
    assert runsubcommand.main([], args) == 1
    assert "could not acquire scheduler lock:" in caplog.text


def test_reload_config_replaces_backups(monkeypatch, runsubcommand, caplog):
    caplog.set_level(logging.INFO)
    config_file = Path("/tmp/config.yaml")
    backups = [MagicMock()]
    scheduler = MagicMock()
    monkeypatch.setattr(yaesm.config, "parse_config", lambda _path: backups)

    runsubcommand._reload_config(scheduler, config_file)

    scheduler.replace_backups.assert_called_once_with(backups)
    assert "configuration reloaded" in caplog.text


@pytest.mark.parametrize(
    ("errors", "summary"),
    [
        ([("backup", "bad setting")], "failed with 1 error"),
        (
            [("backup-one", "bad setting"), ("backup-two", "missing setting")],
            "failed with 2 errors",
        ),
    ],
)
def test_reload_config_error_keeps_existing_schedule(
    monkeypatch, runsubcommand, caplog, errors, summary
):
    config_file = Path("/tmp/config.yaml")
    scheduler = MagicMock()

    def fail(_path):
        raise yaesm.config.ConfigErrors(config_file, errors)

    monkeypatch.setattr(yaesm.config, "parse_config", fail)

    runsubcommand._reload_config(scheduler, config_file)

    scheduler.replace_backups.assert_not_called()
    assert f"configuration reload {summary}; keeping existing schedule" in caplog.text
    for backup, error in errors:
        assert f"    {backup}: {error}" in caplog.text


def test_sighup_reloads_config(monkeypatch, tmp_path):
    monkeypatch.setattr(yaesm.cleanup.Cleanup, "add_function", lambda _fn: None)
    handlers = {}

    def set_signal(signum, handler):
        return handlers.setdefault(signum, handler)

    monkeypatch.setattr(signal, "signal", set_signal)

    scheduler = MagicMock()
    monkeypatch.setattr(yaesm.scheduler, "Scheduler", lambda: scheduler)
    subcommand = RunSubcommand()
    reload_config = MagicMock()
    monkeypatch.setattr(subcommand, "_reload_config", reload_config)

    def start():
        handlers[signal.SIGHUP](signal.SIGHUP, None)
        raise KeyboardInterrupt

    scheduler.start.side_effect = start

    import argparse

    parser = argparse.ArgumentParser()
    RunSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args(["--lockfile", str(tmp_path / "scheduler.lock")])
    args.config = tmp_path / "config.yaml"

    assert subcommand.main([], args) == 0
    reload_config.assert_called_once_with(scheduler, args.config)
    os.close(subcommand._lock_fd)


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit()])
def test_start_keyboardinterrupt_or_systemexit_returns_0_and_logs(
    monkeypatch, tmp_path, caplog, exc
):
    caplog.set_level(logging.INFO)

    monkeypatch.setattr(yaesm.cleanup.Cleanup, "add_function", lambda _fn: None)
    monkeypatch.setattr(signal, "signal", lambda *_args: None)

    sched = MagicMock()
    sched.start.side_effect = exc
    monkeypatch.setattr(yaesm.scheduler, "Scheduler", lambda: sched)

    import argparse

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

    monkeypatch.setattr(yaesm.cleanup.Cleanup, "add_function", lambda _fn: None)
    monkeypatch.setattr(signal, "signal", lambda *_args: None)

    sched = MagicMock()
    sched.start.side_effect = RuntimeError("boom")
    monkeypatch.setattr(yaesm.scheduler, "Scheduler", lambda: sched)

    import argparse

    parser = argparse.ArgumentParser()
    RunSubcommand.add_argparser_arguments(parser)
    args = parser.parse_args(["--lockfile", str(tmp_path / "scheduler.lock")])

    subcmd = RunSubcommand()
    rc = subcmd.main([], args)

    assert rc == 1
    assert "scheduler crashed" in caplog.text

    os.close(subcmd._lock_fd)
