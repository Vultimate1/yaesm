"""Tests for yaesm.subcommand.runsubcommand."""

import argparse
import fcntl
import logging
import signal
from pathlib import Path
from unittest import mock
from uuid import UUID

import pytest

import yaesm.subcommand.runsubcommand as run_module
from yaesm.config import Config, ConfigError
from yaesm.control import ControlError
from yaesm.errors import YaesmError
from yaesm.scheduler import SchedulerError
from yaesm.subcommand.runsubcommand import RunError, RunSubcommand
from yaesm.subcommand.subcommandbase import TargetSelectionMode

_REQUEST_ID = UUID("11111111-1111-1111-1111-111111111111")


def arguments(tmp_path: Path) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=tmp_path / "config.yaml")
    RunSubcommand.configure_argparser(parser)
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

    assert RunSubcommand.target_selection is TargetSelectionMode.NONE
    assert parsed.lockfile == tmp_path / "yaesm.lock"
    assert parsed.control_socket == tmp_path / "control.sock"
    assert parsed.stderr_timestamps


def test_run_can_disable_stderr_timestamps():
    parser = argparse.ArgumentParser()
    RunSubcommand.configure_argparser(parser)

    assert not parser.parse_args(["--no-stderr-timestamps"]).stderr_timestamps


def test_run_uses_default_lockfile():
    parser = argparse.ArgumentParser()
    RunSubcommand.configure_argparser(parser)

    assert parser.parse_args([]).lockfile == Path("/run/lock/yaesm-run.lock")
    assert parser.parse_args([]).control_socket == Path("/run/yaesm/control.sock")


def test_control_request_enqueues_backup():
    scheduler = mock.Mock()
    scheduler.enqueue_targets.return_value = _REQUEST_ID
    scheduler.request_messages.return_value = iter(
        ({"type": "result", "ok": True, "request_id": str(_REQUEST_ID)},)
    )

    messages = RunSubcommand._control_request(
        scheduler,
        Path("config.yaml"),
        {"command": "backup", "targets": ["local", "home", "local"], "schedule": "manual"},
    )

    scheduler.enqueue_targets.assert_called_once_with(("local", "home"), "manual")
    scheduler.request_messages.assert_called_once_with(_REQUEST_ID)
    assert tuple(messages) == ({"type": "result", "ok": True, "request_id": str(_REQUEST_ID)},)


def test_control_request_uses_default_on_demand_schedule():
    scheduler = mock.Mock()
    scheduler.enqueue_targets.return_value = _REQUEST_ID

    RunSubcommand._control_request(
        scheduler,
        Path("config.yaml"),
        {"command": "backup", "targets": ["home"]},
    )

    scheduler.enqueue_targets.assert_called_once_with(("home",), None)


@pytest.mark.parametrize(
    ("control_request", "error"),
    [
        ({"command": "backup", "schedule": "manual"}, "requires backup targets"),
        ({"command": "backup", "targets": []}, "requires backup targets"),
        ({"command": "backup", "targets": "home"}, "requires backup targets"),
        ({"command": "backup", "targets": [""]}, "requires backup targets"),
        ({"command": "backup", "targets": ["@all", "home"]}, "cannot be combined"),
        ({"command": "backup", "targets": ["home"], "schedule": ""}, "nonempty string"),
        (
            {"command": "backup", "targets": ["home"], "schedule": "manual", "extra": True},
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
    assert messages == ({"type": "result", "ok": True, "request_id": None},)


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
    scheduler.enqueue_targets.return_value = _REQUEST_ID
    scheduler.request_messages.return_value = (
        {"type": "result", "ok": True, "request_id": str(_REQUEST_ID)},
    )
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
    assert tuple(handler({"command": "backup", "targets": ["home"], "schedule": "manual"})) == (
        {"type": "result", "ok": True, "request_id": str(_REQUEST_ID)},
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


def test_shutdown_signal_starts_shutdown_thread(monkeypatch, tmp_path):
    handlers = {}
    scheduler = mock.Mock()
    thread = mock.Mock()
    thread_type = mock.Mock(return_value=thread)
    monkeypatch.setattr(run_module, "Scheduler", mock.Mock(return_value=scheduler))
    monkeypatch.setattr(run_module, "Thread", thread_type)
    monkeypatch.setattr(
        signal, "signal", lambda signum, handler: handlers.setdefault(signum, handler)
    )
    RunSubcommand().main(Config({}, {}), arguments(tmp_path))

    handlers[signal.SIGTERM](signal.SIGTERM, None)

    thread_type.assert_called_once_with(target=scheduler.stop, name="yaesm-shutdown")
    thread.start.assert_called_once_with()


def test_second_shutdown_signal_forces_shutdown(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    handlers = {}
    scheduler = mock.Mock()
    force_shutdown = mock.Mock()
    threads = (mock.Mock(), mock.Mock())
    thread_type = mock.Mock(side_effect=threads)
    monkeypatch.setattr(run_module, "Scheduler", mock.Mock(return_value=scheduler))
    monkeypatch.setattr(run_module, "_force_shutdown", force_shutdown)
    monkeypatch.setattr(run_module, "Thread", thread_type)
    monkeypatch.setattr(
        signal, "signal", lambda signum, handler: handlers.setdefault(signum, handler)
    )

    def signal_shutdown():
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        handlers[signal.SIGINT](signal.SIGINT, None)
        handlers[signal.SIGTERM](signal.SIGTERM, None)

    scheduler.start.side_effect = signal_shutdown

    assert RunSubcommand().main(Config({}, {}), arguments(tmp_path)) == 1

    assert thread_type.call_args_list == [
        mock.call(target=scheduler.stop, name="yaesm-shutdown"),
        mock.call(target=force_shutdown, name="yaesm-force-shutdown"),
    ]
    for thread in threads:
        thread.start.assert_called_once_with()
    assert "graceful shutdown requested; waiting for running backups" in caplog.messages
    assert "forced shutdown requested; terminating running backups" in caplog.messages


def test_force_shutdown_cancels_commands_and_exits(monkeypatch):
    cancel_commands = mock.Mock()
    exit_process = mock.Mock(side_effect=SystemExit(1))
    monkeypatch.setattr(run_module, "cancel_commands", cancel_commands)
    monkeypatch.setattr(run_module.os, "_exit", exit_process)

    with pytest.raises(SystemExit, match="1"):
        run_module._force_shutdown()

    cancel_commands.assert_called_once_with()
    exit_process.assert_called_once_with(1)


def test_force_shutdown_exits_if_command_cancellation_fails(monkeypatch):
    monkeypatch.setattr(
        run_module,
        "cancel_commands",
        mock.Mock(side_effect=RuntimeError("failed")),
    )
    exit_process = mock.Mock(side_effect=SystemExit(1))
    monkeypatch.setattr(run_module.os, "_exit", exit_process)

    with pytest.raises(SystemExit, match="1"):
        run_module._force_shutdown()

    exit_process.assert_called_once_with(1)


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


def test_reload_during_shutdown_keeps_current_config(monkeypatch, tmp_path, caplog):
    config = Config({}, {})
    monkeypatch.setattr(run_module, "parse_config", mock.Mock(return_value=config))
    scheduler = mock.Mock()
    scheduler.replace_config.side_effect = SchedulerError("scheduler is stopping")

    assert RunSubcommand._reload_config(scheduler, tmp_path / "config.yaml") == (
        "scheduler is stopping"
    )

    scheduler.replace_config.assert_called_once_with(config)
    assert caplog.messages == [
        "configuration reload failed; keeping current configuration\n  scheduler is stopping"
    ]
