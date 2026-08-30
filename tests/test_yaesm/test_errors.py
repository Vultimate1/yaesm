"""Tests for yaesm.errors."""

import pytest

from yaesm.backup import BackupError
from yaesm.command import CommandError
from yaesm.config import BackupTargetError, ConfigError
from yaesm.control import ControlError
from yaesm.driver.btrfsdriver import BtrfsDriverError
from yaesm.driver.driverbase import DriverError
from yaesm.driver.rsyncdriver import RsyncDriverError
from yaesm.driver.zfsdriver import ZFSDriverError
from yaesm.errors import YaesmError, YaesmValueError
from yaesm.pipeline import PipelineError
from yaesm.scheduler import SchedulerError
from yaesm.ssh import SSHTargetError
from yaesm.subcommand.checksubcommand import CheckError
from yaesm.subcommand.findsubcommand import FindError, FindQueryError
from yaesm.subcommand.runsubcommand import RunError


@pytest.mark.parametrize(
    ("error_type", "parents"),
    [
        (BackupError, (YaesmError,)),
        (DriverError, (BackupError, YaesmError)),
        (PipelineError, (BackupError, YaesmError)),
        (CommandError, (YaesmError,)),
        (ConfigError, (YaesmError,)),
        (BackupTargetError, (YaesmValueError, YaesmError, ValueError)),
        (ControlError, (YaesmError,)),
        (SchedulerError, (YaesmError,)),
        (CheckError, (YaesmError,)),
        (FindError, (YaesmValueError, YaesmError, ValueError)),
        (FindQueryError, (FindError, YaesmValueError, YaesmError, ValueError)),
        (RunError, (YaesmError,)),
        (YaesmValueError, (YaesmError, ValueError)),
        (SSHTargetError, (YaesmValueError, YaesmError, ValueError)),
        (BtrfsDriverError, (DriverError, BackupError, YaesmError)),
        (RsyncDriverError, (DriverError, BackupError, YaesmError)),
        (ZFSDriverError, (DriverError, BackupError, YaesmError)),
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
        BackupTargetError("target"),
        ControlError("control"),
        SchedulerError("scheduler"),
        CheckError("check"),
        FindError("find"),
        FindQueryError("query"),
        RunError("run"),
        YaesmValueError("value"),
        SSHTargetError("SSH target"),
        BtrfsDriverError("Btrfs"),
        RsyncDriverError("Rsync"),
        ZFSDriverError("ZFS"),
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
        (BackupTargetError, "yaesm.config"),
        (ControlError, "yaesm.control"),
        (SchedulerError, "yaesm.scheduler"),
        (CheckError, "yaesm.subcommand.checksubcommand"),
        (FindError, "yaesm.subcommand.findsubcommand"),
        (FindQueryError, "yaesm.subcommand.findsubcommand"),
        (RunError, "yaesm.subcommand.runsubcommand"),
        (YaesmValueError, "yaesm.errors"),
        (DriverError, "yaesm.driver.driverbase"),
        (BtrfsDriverError, "yaesm.driver.btrfsdriver"),
        (RsyncDriverError, "yaesm.driver.rsyncdriver"),
        (ZFSDriverError, "yaesm.driver.zfsdriver"),
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
