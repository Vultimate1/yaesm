"""Tests for yaesm.check."""

from yaesm.check import Check, CheckResult, CheckRole


def test_check_roles():
    assert {role.value for role in CheckRole} == {"source", "transform", "destination"}


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
    assert calls == []
    assert check.run() is result
    assert calls == [True]
