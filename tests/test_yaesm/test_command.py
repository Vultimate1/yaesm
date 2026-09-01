"""Tests for yaesm.command."""

import logging
import shlex
import subprocess
import sys
from pathlib import Path
from threading import Event, Thread
from unittest import mock

import pytest

import yaesm.command as command_module
from yaesm.command import (
    CommandCancelledError,
    CommandError,
    CommandRunner,
    CommandStage,
)
from yaesm.logging import current_backup
from yaesm.ssh import SSHTarget


def test_command_stage_normalizes_its_command():
    stage = CommandStage(("command", Path("argument")))

    assert stage.command == ("command", "argument")
    assert stage.ssh is None
    assert stage.execution_command() == stage.command


def test_command_stage_builds_ssh_command(tmp_path):
    ssh = SSHTarget("ssh://host", tmp_path / "identity")
    stage = CommandStage(("command", "argument"), ssh)

    assert stage.execution_command() == ssh.openssh_command(stage.command)


def test_pipeline_logs_commands(caplog):
    commands = [
        [sys.executable, "-c", "print('hello world')"],
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
    ]

    with caplog.at_level(logging.DEBUG, logger="yaesm.command"):
        CommandRunner().pipeline(commands)

    assert caplog.messages == ["exec: " + " | ".join(shlex.join(command) for command in commands)]


def test_pipeline_logs_status_while_running(monkeypatch, caplog):
    monkeypatch.setattr(command_module, "_STATUS_LOG_INTERVAL_SECONDS", 0.01)
    token = current_backup.set("backup 'home' (manual)")

    try:
        with caplog.at_level(logging.INFO, logger="yaesm.command"):
            CommandRunner().pipeline([[sys.executable, "-c", "import time; time.sleep(0.04)"]])
    finally:
        current_backup.reset(token)

    assert caplog.messages
    assert all(
        message.startswith("backup 'home' (manual): command pipeline still running (")
        and message.endswith(" elapsed)")
        for message in caplog.messages
    )


def test_run_captures_output():
    result = CommandRunner().run(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
        capture_output=True,
    )

    assert result.stdout == "out\n"
    assert result.stderr == "err"
    assert result.returncode == 0
    assert result.returncodes == (0,)


def test_run_discards_uncaptured_output(capfd):
    result = CommandRunner().run([sys.executable, "-c", "print('unmanaged output')"])

    assert result.stdout is None
    assert capfd.readouterr().out == ""


def test_pipeline_streams_between_commands():
    result = CommandRunner().pipeline(
        [
            [sys.executable, "-c", "print('hello')"],
            [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        ],
        capture_output=True,
    )

    assert result.stdout == "HELLO\n"


def test_pipeline_executes_command_stages():
    result = CommandRunner().pipeline(
        (
            CommandStage((sys.executable, "-c", "print('hello')")),
            CommandStage(
                (sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())")
            ),
        ),
        capture_output=True,
    )

    assert result.stdout == "HELLO\n"


def test_pipeline_starts_commands_in_new_sessions(monkeypatch):
    start_new_session = []
    popen = subprocess.Popen

    def record_option(*args, **kwargs):
        start_new_session.append(kwargs.get("start_new_session"))
        return popen(*args, **kwargs)

    monkeypatch.setattr(command_module.subprocess, "Popen", record_option)

    CommandRunner().pipeline([[sys.executable, "-c", "pass"]])

    assert start_new_session == [True]


def test_cancellation_terminates_active_pipeline_and_prevents_new_commands(monkeypatch):
    monkeypatch.setattr(command_module, "_commands_cancelled", False)
    started = Event()
    processes = []
    signals = []
    errors = []
    popen = subprocess.Popen
    killpg = command_module.os.killpg

    def record_process(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        started.set()
        return process

    def record_signal(pid, signum):
        signals.append((pid, signum))
        killpg(pid, signum)

    def execute():
        try:
            CommandRunner().run([sys.executable, "-c", "import time; time.sleep(60)"])
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(command_module.subprocess, "Popen", record_process)
    monkeypatch.setattr(command_module.os, "killpg", record_signal)
    thread = Thread(target=execute)
    thread.start()
    try:
        assert started.wait(5)
        command_module.cancel_commands()
        thread.join(5)
        command_module.cancel_commands()
    finally:
        if thread.is_alive() and processes:
            processes[0].kill()
            thread.join(5)

    assert not thread.is_alive()
    assert processes[0].returncode is not None
    assert signals == [
        (processes[0].pid, command_module.signal.SIGTERM),
        (processes[0].pid, command_module.signal.SIGKILL),
    ]
    assert len(errors) == 1
    assert isinstance(errors[0], CommandCancelledError)

    with pytest.raises(CommandCancelledError, match="forced shutdown"):
        CommandRunner().run([sys.executable, "-c", "pass"])


def test_termination_escalates_to_sigkill(monkeypatch):
    process = mock.Mock(pid=123, stdout=None)
    process.poll.return_value = None
    process.wait.side_effect = (subprocess.TimeoutExpired(("command",), 5), None)
    killpg = mock.Mock()
    monkeypatch.setattr(command_module.os, "killpg", killpg)

    command_module._terminate((process,))

    assert killpg.call_args_list == [
        mock.call(123, command_module.signal.SIGTERM),
        mock.call(123, command_module.signal.SIGKILL),
    ]
    assert process.wait.call_count == 2


def test_execution_groups_adjacent_stages_on_the_same_ssh_connection(tmp_path):
    ssh = SSHTarget("ssh://host", tmp_path / "identity")
    stages = (
        CommandStage(("produce", "data"), ssh),
        CommandStage(("transform", "data"), ssh),
    )

    assert command_module._execution_commands(stages) == (
        ssh.openssh_pipeline(tuple(stage.command for stage in stages)),
    )


def test_execution_separates_local_and_different_ssh_stages(tmp_path):
    first_ssh = SSHTarget("ssh://first", tmp_path / "first-key")
    second_ssh = SSHTarget("ssh://second", tmp_path / "second-key")

    assert command_module._execution_commands(
        (
            CommandStage(("first",), first_ssh),
            CommandStage(("second",), first_ssh),
            CommandStage(("local",)),
            CommandStage(("third",), second_ssh),
            ("raw",),
        )
    ) == (
        first_ssh.openssh_pipeline((("first",), ("second",))),
        ("local",),
        second_ssh.openssh_command(("third",)),
        ("raw",),
    )


def test_execution_does_not_group_different_ssh_configuration(tmp_path):
    first = SSHTarget("ssh://host", tmp_path / "first-key")
    second = SSHTarget("ssh://host", tmp_path / "second-key")

    assert command_module._execution_commands(
        (CommandStage(("first",), first), CommandStage(("second",), second))
    ) == (first.openssh_command(("first",)), second.openssh_command(("second",)))


def test_pipeline_reports_failed_command():
    failed = [
        sys.executable,
        "-c",
        "import sys; print('failure', file=sys.stderr); sys.exit(7)",
    ]

    with pytest.raises(CommandError) as error:
        CommandRunner().pipeline(
            [
                [sys.executable, "-c", "print('input')"],
                failed,
            ]
        )

    assert error.value.command == tuple(failed)
    assert error.value.returncode == 7
    assert error.value.stderr == "failure\n"
    assert "command exited with status 7" in str(error.value)
    assert "failure" in str(error.value)


def test_pipeline_checks_nonfinal_command():
    failed = [sys.executable, "-c", "import sys; sys.exit(6)"]

    with pytest.raises(CommandError) as error:
        CommandRunner().pipeline(
            [
                failed,
                [sys.executable, "-c", "import sys; sys.stdin.read()"],
            ]
        )

    assert error.value.command == tuple(failed)
    assert error.value.returncode == 6


def test_run_allows_failure():
    result = CommandRunner().run([sys.executable, "-c", "import sys; sys.exit(5)"], check=False)

    assert result.returncode == 5
    assert result.returncodes == (5,)


def test_pipeline_reports_all_statuses_without_checking():
    result = CommandRunner().pipeline(
        [
            [sys.executable, "-c", "import sys; sys.exit(6)"],
            [sys.executable, "-c", "import sys; sys.stdin.read(); sys.exit(7)"],
        ],
        check=False,
    )

    assert result.returncode == 7
    assert result.returncodes == (6, 7)


def test_run_reports_command_that_cannot_start(tmp_path):
    missing = [tmp_path / "missing-command"]

    with pytest.raises(CommandError) as error:
        CommandRunner().run(missing)

    assert error.value.command == (str(missing[0]),)
    assert error.value.returncode is None
    assert "could not start command" in str(error.value)


def test_pipeline_terminates_process_when_next_command_cannot_start(tmp_path, monkeypatch):
    started = []
    popen = subprocess.Popen

    def record_process(*args, **kwargs):
        process = popen(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(command_module.subprocess, "Popen", record_process)

    with pytest.raises(CommandError):
        CommandRunner().pipeline(
            [
                [sys.executable, "-c", "import time; time.sleep(60)"],
                [tmp_path / "missing-command"],
            ]
        )

    assert len(started) == 1
    assert started[0].returncode is not None


def test_pipeline_terminates_process_when_interrupted(monkeypatch):
    started = []
    popen = subprocess.Popen

    def interrupt_second_process(*args, **kwargs):
        if started:
            raise KeyboardInterrupt
        process = popen(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(command_module.subprocess, "Popen", interrupt_second_process)

    with pytest.raises(KeyboardInterrupt):
        CommandRunner().pipeline(
            [
                [sys.executable, "-c", "import time; time.sleep(60)"],
                [sys.executable, "-c", "pass"],
            ]
        )

    assert started[0].returncode is not None


def test_pipeline_reports_rightmost_failure():
    rightmost = [sys.executable, "-c", "import sys; sys.stdin.read(); sys.exit(7)"]

    with pytest.raises(CommandError) as error:
        CommandRunner().pipeline(
            [
                [sys.executable, "-c", "import sys; sys.exit(6)"],
                rightmost,
            ]
        )

    assert error.value.command == tuple(rightmost)
    assert error.value.returncode == 7


def test_pipeline_combines_stderr():
    result = CommandRunner().pipeline(
        [
            [
                sys.executable,
                "-c",
                "import sys; print('first', file=sys.stderr); print('input')",
            ],
            [
                sys.executable,
                "-c",
                "import sys; sys.stdin.read(); print('second', file=sys.stderr)",
            ],
        ]
    )

    assert result.stderr == "first\nsecond"


def test_pipeline_rejects_no_commands():
    with pytest.raises(ValueError, match="pipeline cannot be empty"):
        CommandRunner().pipeline([])


def test_pipeline_rejects_empty_command():
    with pytest.raises(ValueError, match="command cannot be empty"):
        CommandRunner().pipeline([[sys.executable, "-c", "pass"], []])
