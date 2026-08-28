"""Tests for yaesm.subcommand.runsubcommand."""

import argparse
import fcntl
import logging
import signal
from pathlib import Path
from unittest import mock

import pytest

import yaesm.subcommand.runsubcommand as run_module
from yaesm.config import Config, ConfigError
from yaesm.control import ControlError
from yaesm.errors import YaesmError
from yaesm.subcommand.runsubcommand import RunError, RunSubcommand


def arguments(tmp_path: Path) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=tmp_path / "config.yaml")
    RunSubcommand.add_argparser_arguments(parser)
    return parser.parse_args(
        [
            "--lockfile",
            str(tmp_path / "yaesm.lock"),
            "--control-socket",
            str(tmp_path / "control.sock"),
        ]
    )


def test_run_error_is_expected_error():
    assert issubclass(RunError, YaesmError)


def test_run_arguments(tmp_path):
    parsed = arguments(tmp_path)

    assert parsed.lockfile == tmp_path / "yaesm.lock"
    assert parsed.control_socket == tmp_path / "control.sock"


def test_run_uses_default_lockfile():
    parser = argparse.ArgumentParser()
    RunSubcommand.add_argparser_arguments(parser)

    assert parser.parse_args([]).lockfile == Path("/run/lock/yaesm-run.lock")
    assert parser.parse_args([]).control_socket == Path("/run/yaesm/control.sock")


def test_control_request_enqueues_backup():
    scheduler = mock.Mock()
    scheduler.enqueue_backup.return_value = "request-id"

    messages = RunSubcommand._control_request(
        scheduler,
        Path("config.yaml"),
        {"command": "backup", "backup": "home", "schedule": "manual"},
    )

    scheduler.enqueue_backup.assert_called_once_with("home", "manual")
    assert messages == ({"type": "result", "ok": True, "request_id": "request-id"},)


@pytest.mark.parametrize(
    ("control_request", "error"),
    [
        ({"command": "backup", "schedule": "manual"}, "requires a backup name"),
        ({"command": "backup", "backup": "home"}, "requires a schedule name"),
        (
            {"command": "backup", "backup": "home", "schedule": "manual", "extra": True},
            "unknown fields: extra",
        ),
        ({"command": "reload-config", "extra": True}, "unknown fields: extra"),
    ],
)
def test_control_request_rejects_invalid_fields(control_request, error):
    with pytest.raises(ControlError, match=error):
        RunSubcommand._control_request(mock.Mock(), Path("config.yaml"), control_request)


@pytest.mark.parametrize(
    ("control_request", "error"),
    [
        ({}, "requires a command"),
        ({"command": "unknown"}, "unknown control command: 'unknown'"),
    ],
)
def test_control_request_rejects_invalid_command(control_request, error):
    with pytest.raises(ControlError, match=error):
        RunSubcommand._control_request(mock.Mock(), Path("config.yaml"), control_request)


def test_control_request_reloads_config(monkeypatch):
    scheduler = mock.Mock()
    reload_config = mock.Mock(return_value=None)
    monkeypatch.setattr(RunSubcommand, "_reload_config", reload_config)
    path = Path("config.yaml")

    messages = RunSubcommand._control_request(scheduler, path, {"command": "reload-config"})

    reload_config.assert_called_once_with(scheduler, path)
    assert messages == ({"type": "result", "ok": True},)


def test_control_request_reports_reload_error(monkeypatch):
    monkeypatch.setattr(
        RunSubcommand,
        "_reload_config",
        mock.Mock(return_value="invalid configuration"),
    )

    with pytest.raises(ControlError, match="invalid configuration"):
        RunSubcommand._control_request(
            mock.Mock(),
            Path("config.yaml"),
            {"command": "reload-config"},
        )


def test_run_starts_and_stops_scheduler(monkeypatch, tmp_path):
    scheduler = mock.Mock()
    scheduler.enqueue_backup.return_value = "request-id"
    scheduler_type = mock.Mock(return_value=scheduler)
    control = mock.MagicMock()
    control_type = mock.Mock(return_value=control)
    monkeypatch.setattr(run_module, "Scheduler", scheduler_type)
    monkeypatch.setattr(run_module, "ControlServer", control_type)
    monkeypatch.setattr(signal, "signal", mock.Mock())
    config = Config({}, {})

    assert RunSubcommand().main(config, arguments(tmp_path)) == 0

    scheduler_type.assert_called_once_with(config)
    control_type.assert_called_once()
    path, handler = control_type.call_args.args
    assert path == tmp_path / "control.sock"
    assert handler({"command": "backup", "backup": "home", "schedule": "manual"}) == (
        {"type": "result", "ok": True, "request_id": "request-id"},
    )
    scheduler.start.assert_called_once_with()
    scheduler.stop.assert_called_once_with()


def test_run_stops_scheduler_after_interruption(monkeypatch, tmp_path):
    scheduler = mock.Mock()
    scheduler.start.side_effect = KeyboardInterrupt
    monkeypatch.setattr(run_module, "Scheduler", mock.Mock(return_value=scheduler))
    monkeypatch.setattr(signal, "signal", mock.Mock())

    assert RunSubcommand().main(Config({}, {}), arguments(tmp_path)) == 0

    scheduler.stop.assert_called_once_with()


def test_run_stops_scheduler_after_failure(monkeypatch, tmp_path):
    scheduler = mock.Mock()
    scheduler.start.side_effect = RuntimeError("boom")
    monkeypatch.setattr(run_module, "Scheduler", mock.Mock(return_value=scheduler))
    monkeypatch.setattr(signal, "signal", mock.Mock())

    with pytest.raises(RuntimeError, match="boom"):
        RunSubcommand().main(Config({}, {}), arguments(tmp_path))

    scheduler.stop.assert_called_once_with()


def test_run_rejects_locked_scheduler(monkeypatch, tmp_path):
    def fail(*_args):
        raise BlockingIOError("already locked")

    monkeypatch.setattr(fcntl, "lockf", fail)
    parsed = arguments(tmp_path)

    with pytest.raises(RunError, match=f"could not acquire scheduler lock {parsed.lockfile}"):
        RunSubcommand().main(Config({}, {}), parsed)


def test_run_reports_lockfile_open_failure(monkeypatch, tmp_path):
    parsed = arguments(tmp_path)
    monkeypatch.setattr(Path, "open", mock.Mock(side_effect=PermissionError("denied")))

    with pytest.raises(RunError, match=f"could not open scheduler lock {parsed.lockfile}"):
        RunSubcommand().main(Config({}, {}), parsed)


def test_run_registers_signal_handlers(monkeypatch, tmp_path):
    handlers = {}
    scheduler = mock.Mock()
    monkeypatch.setattr(run_module, "Scheduler", mock.Mock(return_value=scheduler))
    monkeypatch.setattr(
        signal, "signal", lambda signum, handler: handlers.setdefault(signum, handler)
    )

    RunSubcommand().main(Config({}, {}), arguments(tmp_path))

    assert set(handlers) == {signal.SIGHUP, signal.SIGTERM, signal.SIGINT}


def test_reload_config(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    config = Config({}, {})
    scheduler = mock.Mock()
    monkeypatch.setattr(run_module, "parse_config", mock.Mock(return_value=config))

    assert RunSubcommand._reload_config(scheduler, tmp_path / "config.yaml") is None

    scheduler.replace_config.assert_called_once_with(config)
    assert "configuration reloaded" in caplog.messages


def test_reload_invalid_config_keeps_current_jobs(monkeypatch, tmp_path, caplog):
    error = ConfigError(("first error", "second error"))
    monkeypatch.setattr(run_module, "parse_config", mock.Mock(side_effect=error))
    scheduler = mock.Mock()

    assert RunSubcommand._reload_config(scheduler, tmp_path / "config.yaml") == error.format()

    scheduler.replace_config.assert_not_called()
    records = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(records) == 1
    assert records[0].getMessage() == (
        "configuration reload failed; keeping current configuration\n"
        "  configuration errors:\n"
        "    - first error\n"
        "    - second error"
    )
