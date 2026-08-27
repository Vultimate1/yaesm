"""Tests for yaesm.errors."""

import pytest

from yaesm.backup import BackupError
from yaesm.command import CommandError
from yaesm.config import ConfigError
from yaesm.driver.btrfsdriver import BtrfsDriverError
from yaesm.driver.driverbase import DriverError
from yaesm.driver.rsyncdriver import RsyncDriverError
from yaesm.errors import YaesmError
from yaesm.pipeline import PipelineError
from yaesm.ssh import SSHTargetError


@pytest.mark.parametrize(
    ("error_type", "parents"),
    [
        (BackupError, (YaesmError,)),
        (DriverError, (BackupError, YaesmError)),
        (PipelineError, (BackupError, YaesmError)),
        (CommandError, (YaesmError,)),
        (ConfigError, (YaesmError,)),
        (SSHTargetError, (YaesmError, ValueError)),
        (BtrfsDriverError, (DriverError, BackupError, YaesmError)),
        (RsyncDriverError, (DriverError, BackupError, YaesmError)),
    ],
)
def test_error_hierarchy(error_type, parents):
    assert all(issubclass(error_type, parent) for parent in parents)


@pytest.mark.parametrize(
    "error",
    [
        BackupError("backup"),
        DriverError("driver"),
        PipelineError("pipeline"),
        CommandError(("false",), 1, ""),
        ConfigError("config"),
        SSHTargetError("SSH target"),
        BtrfsDriverError("Btrfs"),
        RsyncDriverError("Rsync"),
    ],
)
def test_all_expected_errors_can_be_caught_together(error):
    with pytest.raises(YaesmError) as caught:
        raise error

    assert caught.value is error


@pytest.mark.parametrize(
    ("error_type", "module"),
    [
        (BackupError, "yaesm.backup"),
        (CommandError, "yaesm.command"),
        (ConfigError, "yaesm.config"),
        (DriverError, "yaesm.driver.driverbase"),
        (BtrfsDriverError, "yaesm.driver.btrfsdriver"),
        (RsyncDriverError, "yaesm.driver.rsyncdriver"),
        (PipelineError, "yaesm.pipeline"),
        (SSHTargetError, "yaesm.ssh"),
    ],
)
def test_specific_errors_are_owned_by_their_modules(error_type, module):
    assert error_type.__module__ == module


def test_error_format_includes_expected_causes():
    command = CommandError(("example",), 7, "failed")
    driver = DriverError("driver failed")
    driver.__cause__ = command
    backup = BackupError("backup failed")
    backup.__cause__ = driver

    assert backup.format() == (
        "backup failed\n  driver failed\n    command exited with status 7: example\n      failed"
    )


def test_error_format_ignores_unexpected_cause():
    error = BackupError("backup failed")
    error.__cause__ = ValueError("internal detail")

    assert error.format() == "backup failed"
