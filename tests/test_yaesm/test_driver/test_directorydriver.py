"""Tests for yaesm.driver.directorydriver."""

from pathlib import Path

import pytest
import voluptuous as vlp

import yaesm.command as command_module
from yaesm.check import CheckRole
from yaesm.command import Command, CommandResult
from yaesm.driver.directorydriver import DirectoryDriver
from yaesm.representation import PathTree
from yaesm.ssh import SSHTarget


class RecordingRunner:
    def __init__(self, returncode: int = 0) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.returncode = returncode

    def run(
        self,
        command: Command,
        *,
        capture_output: bool = False,
        check: bool = True,
    ) -> CommandResult:
        self.commands.append(tuple(str(argument) for argument in command))
        return CommandResult(None, "", (self.returncode,))


def test_name():
    assert DirectoryDriver.name() == "directory"


@pytest.mark.parametrize(
    "value",
    [
        "/source",
        Path("/source"),
    ],
)
def test_config_schema_accepts_absolute_location(value):
    assert DirectoryDriver.config_schema()(value) == {"location": Path("/source")}


@pytest.mark.parametrize(
    "value",
    [
        "relative",
        Path("relative"),
        {"location": "/source"},
        {"location": "relative"},
        {"location": 1},
        {},
        {"location": "/source", "unknown": True},
    ],
)
def test_config_schema_rejects_invalid_configuration(value):
    with pytest.raises(vlp.Invalid):
        DirectoryDriver.config_schema()(value)


def test_cap_source(tmp_path):
    driver = DirectoryDriver(tmp_path)

    assert driver.capabilities() == {"source"}
    assert driver.cap_source() == PathTree(tmp_path)


def test_cap_source_includes_ssh_target(tmp_path):
    target = SSHTarget("ssh://host", tmp_path / "key")

    assert DirectoryDriver(tmp_path, target).cap_source() == PathTree(tmp_path, target)


def test_source_checks_directory_requirements_remotely(tmp_path, monkeypatch):
    target = SSHTarget("ssh://host", tmp_path / "key")
    runner = RecordingRunner()
    monkeypatch.setattr(command_module, "run", runner.run)

    checks = DirectoryDriver(tmp_path, target).check(CheckRole.SOURCE)
    results = tuple(check.run() for check in checks)

    assert tuple(check.description for check in checks) == (
        f"directory exists: {tmp_path} on {target}",
        f"directory is readable: {tmp_path} on {target}",
        f"directory is searchable: {tmp_path} on {target}",
    )
    assert all(result.passed for result in results)
    assert runner.commands == [
        target.openssh_command(("test", "-d", tmp_path)),
        target.openssh_command(("test", "-r", tmp_path)),
        target.openssh_command(("test", "-x", tmp_path)),
    ]


@pytest.mark.parametrize(
    "role",
    [CheckRole.ARTIFACT_SOURCE, CheckRole.TRANSFORM, CheckRole.DESTINATION],
)
def test_non_source_roles_have_no_checks(role, tmp_path):
    assert DirectoryDriver(tmp_path).check(role) == ()


def test_directory_check_reports_failure(tmp_path, monkeypatch):
    runner = RecordingRunner(6)
    monkeypatch.setattr(command_module, "run", runner.run)
    check = DirectoryDriver(tmp_path).check(CheckRole.SOURCE)[0]

    result = check.run()

    assert result.passed is False
    assert result.failure == "test exited with status 6"
