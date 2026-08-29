"""Tests for yaesm.check."""

import pytest

import yaesm.command as command_module
from yaesm.check import Check, CheckResult, CheckRole
from yaesm.command import CommandError, CommandResult
from yaesm.ssh import SSHTarget


def test_check_roles():
    assert {role.value for role in CheckRole} == {
        "source",
        "artifact-source",
        "transform",
        "destination",
    }


def test_check_result_passes_without_failure():
    result = CheckResult("tool is installed")

    assert result.passed is True
    assert result.failure is None
    assert result.stdout is None
    assert result.stderr is None


def test_check_result_fails_with_uniform_message():
    result = CheckResult("tool is installed", "required tool not found: tool")

    assert result.passed is False
    assert result.failure == "required tool not found: tool"


def test_check_result_saves_command_output():
    result = CheckResult(
        "tool runs",
        stdout="standard output",
        stderr="standard error",
    )

    assert result.stdout == "standard output"
    assert result.stderr == "standard error"


def test_check_is_deferred_and_returns_its_result():
    calls = []
    result = CheckResult("tool runs")

    def run():
        calls.append(True)
        return result

    check = Check("tool runs", run)

    assert check.description == "tool runs"
    assert check.ssh is None
    assert calls == []
    assert check.run() is result
    assert calls == [True]


def test_command_check_is_deferred_and_captures_output(tmp_path, monkeypatch):
    calls = []

    def run(command, *, capture_output=False, check=True):
        calls.append((command, capture_output, check))
        return CommandResult("standard output", "standard error", (0,))

    monkeypatch.setattr(command_module, "run", run)
    check = Check.command("tool works", ("tool", tmp_path))

    assert calls == []

    result = check.run()

    assert result == CheckResult("tool works", stdout="standard output", stderr="standard error")
    assert calls == [(("tool", str(tmp_path)), True, False)]


def test_command_check_omits_empty_output(monkeypatch):
    monkeypatch.setattr(
        command_module,
        "run",
        lambda *args, **kwargs: CommandResult("", "", (0,)),
    )

    result = Check.command("tool works", ("tool",)).run()

    assert result == CheckResult("tool works")


@pytest.mark.parametrize("returncode", [1, 2, 127])
def test_command_check_reports_exit_status(returncode, monkeypatch):
    monkeypatch.setattr(
        command_module,
        "run",
        lambda *args, **kwargs: CommandResult("partial output", "failure details", (returncode,)),
    )

    result = Check.command("tool works", ("tool", "argument")).run()

    assert result == CheckResult(
        "tool works",
        f"tool exited with status {returncode}",
        "partial output",
        "failure details",
    )


def test_command_check_can_override_failure_message(monkeypatch):
    monkeypatch.setattr(
        command_module,
        "run",
        lambda *args, **kwargs: CommandResult("", "failure details", (255,)),
    )

    result = Check.command(
        "SSH connection works",
        ("true",),
        failure_message="could not connect",
    ).run()

    assert result.failure == "could not connect"


def test_command_check_reports_start_failure(monkeypatch):
    def run(*args, **kwargs):
        raise CommandError(("tool",), None, "No such file or directory")

    monkeypatch.setattr(command_module, "run", run)

    result = Check.command("tool works", ("tool",)).run()

    assert result == CheckResult(
        "tool works",
        "could not start tool",
        stderr="No such file or directory",
    )


def test_command_check_uses_validation_failure(monkeypatch):
    command_result = CommandResult("invalid value\n", "warning", (0,))
    validated = []
    monkeypatch.setattr(command_module, "run", lambda *args, **kwargs: command_result)

    def validate(result):
        validated.append(result)
        return "output is invalid"

    result = Check.command("tool works", ("tool",), validate=validate).run()

    assert validated == [command_result]
    assert result == CheckResult(
        "tool works",
        "output is invalid",
        "invalid value\n",
        "warning",
    )


def test_command_check_accepts_validated_output(monkeypatch):
    command_result = CommandResult("valid value\n", "", (0,))
    monkeypatch.setattr(command_module, "run", lambda *args, **kwargs: command_result)

    result = Check.command("tool works", ("tool",), validate=lambda result: None).run()

    assert result == CheckResult("tool works", stdout="valid value\n")


def test_command_check_does_not_validate_failed_command(monkeypatch):
    validated = []
    monkeypatch.setattr(
        command_module,
        "run",
        lambda *args, **kwargs: CommandResult("invalid value\n", "failure", (3,)),
    )

    result = Check.command(
        "tool works",
        ("tool",),
        validate=lambda result: validated.append(result) or "output is invalid",
    ).run()

    assert validated == []
    assert result.failure == "tool exited with status 3"


def test_command_check_runs_on_ssh_target(tmp_path, monkeypatch):
    target = SSHTarget("ssh://user@host:2222", tmp_path / "key")
    calls = []

    def run(command, *, capture_output=False, check=True):
        calls.append(command)
        return CommandResult(None, "remote failure", (4,))

    monkeypatch.setattr(command_module, "run", run)
    check = Check.command("tool works", ("tool", "argument"), ssh=target)
    result = check.run()

    assert result == CheckResult(
        f"tool works on {target}",
        "tool exited with status 4",
        stderr="remote failure",
    )
    assert check.ssh is target
    assert calls == [target.openssh_command(("tool", "argument"))]


def test_remote_command_check_reports_local_ssh_start_failure(tmp_path, monkeypatch):
    target = SSHTarget("ssh://host", tmp_path / "key")

    def run(*args, **kwargs):
        raise CommandError(("ssh",), None, "No such file or directory")

    monkeypatch.setattr(command_module, "run", run)

    result = Check.command("tool works", ("tool",), ssh=target).run()

    assert result.failure == "could not start ssh"
