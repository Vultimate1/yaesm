"""Tests for yaesm.subcommand.checksubcommand."""

import argparse
from unittest import mock

import pytest

import yaesm.command as command_module
import yaesm.subcommand.checksubcommand as check_module
from yaesm.backup import Backup, BackupSource
from yaesm.check import Check, CheckResult, CheckRole
from yaesm.command import CommandResult
from yaesm.config import Config
from yaesm.driver.btrfsdriver import BtrfsDriver
from yaesm.errors import YaesmError
from yaesm.ssh import SSHTarget
from yaesm.subcommand.checksubcommand import CheckError, CheckSubcommand


def arguments(*values: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    CheckSubcommand.add_argparser_arguments(parser)
    return parser.parse_args(values)


def configured_backup(
    name: str,
    source,
    destination,
    transforms=(),
) -> Backup:
    return Backup(name, source, destination, transforms)


def deferred_result(
    description: str,
    failure: str | None = None,
    *,
    ssh: SSHTarget | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
) -> tuple[Check, mock.Mock]:
    function = mock.Mock(return_value=CheckResult(description, failure, stdout, stderr))
    return Check(description, function, ssh), function


def test_check_error_is_expected_error():
    assert issubclass(CheckError, YaesmError)


def test_check_arguments():
    assert arguments().backup_names is None
    assert arguments().quiet is False
    assert arguments("home,,root, home", "--quiet").backup_names == ("home", "root")
    assert arguments("home", "--quiet").quiet is True


def test_check_reports_all_results_by_default(capsys):
    source_check, _ = deferred_result("source is ready")
    destination_check, _ = deferred_result(
        "destination is ready",
        "destination check failed",
        stdout="standard output\n",
        stderr="standard error\n",
    )
    source = mock.Mock()
    source.check.return_value = (source_check,)
    destination = mock.Mock()
    destination.check.return_value = (destination_check,)
    backup = configured_backup("home", source, destination)

    assert CheckSubcommand().main(Config({}, {"home": backup}), arguments()) == 1

    assert capsys.readouterr().out == (
        "backup: home\n"
        "    PASS  source is ready\n"
        "    FAIL  destination is ready\n"
        "          destination check failed\n"
        "          standard output\n"
        "          standard error\n"
    )


def test_quiet_check_reports_only_failures(capsys):
    passed_check, _ = deferred_result("source is ready")
    failed_check, _ = deferred_result("destination is ready", "permission denied")
    source = mock.Mock()
    source.check.return_value = (passed_check,)
    destination = mock.Mock()
    destination.check.return_value = (failed_check,)
    backup = configured_backup("home", source, destination)

    assert CheckSubcommand().main(Config({}, {"home": backup}), arguments("--quiet")) == 1

    assert capsys.readouterr().out == (
        "backup: home\n    FAIL  destination is ready\n          permission denied\n"
    )


def test_quiet_check_prints_nothing_when_checks_pass(capsys):
    check, _ = deferred_result("ready")
    source = mock.Mock()
    source.check.return_value = (check,)
    destination = mock.Mock()
    destination.check.return_value = ()
    backup = configured_backup("home", source, destination)

    assert CheckSubcommand().main(Config({}, {"home": backup}), arguments("-q")) == 0
    assert capsys.readouterr().out == ""


def test_check_assigns_driver_roles():
    source = mock.Mock()
    source.check.return_value = ()
    transform = mock.Mock()
    transform.check.return_value = ()
    destination = mock.Mock()
    destination.check.return_value = ()
    backup = configured_backup("home", source, destination, (transform,))

    assert CheckSubcommand().main(Config({}, {"home": backup}), arguments("-q")) == 0

    source.check.assert_called_once_with(CheckRole.SOURCE)
    transform.check.assert_called_once_with(CheckRole.TRANSFORM)
    destination.check.assert_called_once_with(CheckRole.DESTINATION)


def test_identical_checks_run_and_print_once(monkeypatch, capsys):
    run = mock.Mock(return_value=CommandResult(None, "", (0,)))
    monkeypatch.setattr(command_module, "run", run)
    source = mock.Mock()
    source.check.return_value = (Check.command("btrfs is installed", ("btrfs", "--version")),)
    destination = mock.Mock()
    destination.check.return_value = (Check.command("btrfs is installed", ("btrfs", "--version")),)
    backup = configured_backup("home", source, destination)

    assert CheckSubcommand().main(Config({}, {"home": backup}), arguments()) == 0

    run.assert_called_once_with(("btrfs", "--version"), capture_output=True, check=False)
    assert capsys.readouterr().out == ("backup: home\n    PASS  btrfs is installed\n")


def test_remote_btrfs_prerequisite_is_checked_once(tmp_path):
    target = SSHTarget("ssh://server", tmp_path / "key")
    backup = Backup(
        "home",
        BtrfsDriver(tmp_path / "source", target),
        BtrfsDriver(tmp_path / "destination", target),
    )
    checks = CheckSubcommand._unique_checks(CheckSubcommand._backup_checks(backup, {}))

    assert (
        tuple(check.description for check in checks).count(f"btrfs is installed on {target}") == 1
    )


def test_remote_endpoints_have_distinct_check_descriptions(tmp_path):
    first = Check.command(
        "btrfs is installed",
        ("btrfs", "--version"),
        ssh=SSHTarget("ssh://first", tmp_path / "key"),
    )
    second = Check.command(
        "btrfs is installed",
        ("btrfs", "--version"),
        ssh=SSHTarget("ssh://second", tmp_path / "key"),
    )

    assert CheckSubcommand._unique_checks((first, second)) == (first, second)


def test_check_rejects_one_description_for_different_checks():
    source = mock.Mock()
    source.check.return_value = (Check.command("storage is ready", ("first",)),)
    destination = mock.Mock()
    destination.check.return_value = (Check.command("storage is ready", ("second",)),)
    backup = configured_backup("home", source, destination)

    with pytest.raises(CheckError, match="ambiguous check description: 'storage is ready'"):
        CheckSubcommand().main(Config({}, {"home": backup}), arguments("-q"))


def test_check_rejects_different_descriptions_for_one_check():
    source = mock.Mock()
    source.check.return_value = (Check.command("first description", ("tool",)),)
    destination = mock.Mock()
    destination.check.return_value = (Check.command("second description", ("tool",)),)
    backup = configured_backup("home", source, destination)

    with pytest.raises(CheckError, match="check has conflicting descriptions"):
        CheckSubcommand().main(Config({}, {"home": backup}), arguments("-q"))


def test_replication_checks_source_backup_destination():
    original_source = mock.Mock()
    original_destination = mock.Mock()
    original_destination.check.return_value = ()
    original = configured_backup("original", original_source, original_destination)
    destination = mock.Mock()
    destination.check.return_value = ()
    replica = Backup("replica", BackupSource("original"), destination)
    config = Config({}, {"original": original, "replica": replica})

    assert CheckSubcommand().main(config, arguments("replica", "-q")) == 0

    original_source.check.assert_not_called()
    original_destination.check.assert_called_once_with(CheckRole.ARTIFACT_SOURCE)
    original_destination.cap_list.assert_not_called()
    destination.check.assert_called_once_with(CheckRole.DESTINATION)


def test_check_selects_requested_backups():
    first_source = mock.Mock()
    first_source.check.return_value = ()
    first_destination = mock.Mock()
    first_destination.check.return_value = ()
    second_source = mock.Mock()
    second_source.check.return_value = ()
    second_destination = mock.Mock()
    second_destination.check.return_value = ()
    second = Backup(
        "second",
        second_source,
        second_destination,
        previous_names=("old-second",),
    )
    config = Config(
        {},
        {
            "first": configured_backup("first", first_source, first_destination),
            "second": second,
        },
    )

    assert CheckSubcommand().main(config, arguments("second,old-second", "-q")) == 0

    first_source.check.assert_not_called()
    second_source.check.assert_called_once_with(CheckRole.SOURCE)


@pytest.mark.parametrize(
    ("value", "error"),
    [(",", "no backup names specified"), ("missing", "unknown backup: 'missing'")],
)
def test_check_rejects_invalid_backup_selection(value, error):
    with pytest.raises(CheckError, match=error):
        CheckSubcommand().main(Config({}, {}), arguments(value))


def test_ssh_preflights_are_reused(monkeypatch, tmp_path):
    target = SSHTarget("ssh://server", tmp_path / "key")
    first_check, first_run = deferred_result("first remote check", ssh=target)
    second_check, second_run = deferred_result("second remote check", ssh=target)
    openssh_check, openssh_run = deferred_result("OpenSSH is installed")
    connection_check, connection_run = deferred_result(
        f"SSH connection works on {target}", ssh=target
    )
    command = mock.Mock(side_effect=(openssh_check, connection_check))
    monkeypatch.setattr(check_module.Check, "command", command)

    first_source = mock.Mock()
    first_source.check.return_value = (first_check,)
    first_destination = mock.Mock()
    first_destination.check.return_value = ()
    second_source = mock.Mock()
    second_source.check.return_value = (second_check,)
    second_destination = mock.Mock()
    second_destination.check.return_value = ()
    config = Config(
        {},
        {
            "first": configured_backup("first", first_source, first_destination),
            "second": configured_backup("second", second_source, second_destination),
        },
    )

    assert CheckSubcommand().main(config, arguments("-q")) == 0

    assert command.call_count == 2
    openssh_run.assert_called_once_with()
    connection_run.assert_called_once_with()
    first_run.assert_called_once_with()
    second_run.assert_called_once_with()


def test_failed_ssh_connection_skips_remote_checks(monkeypatch, tmp_path, capsys):
    target = SSHTarget("ssh://server", tmp_path / "key")
    remote_check, remote_run = deferred_result("remote check", ssh=target)
    local_check, local_run = deferred_result("local check")
    openssh_check, _ = deferred_result("OpenSSH is installed")
    connection_check, _ = deferred_result(
        f"SSH connection works on {target}",
        f"could not connect to {target}",
        ssh=target,
        stderr="connection timed out",
    )
    monkeypatch.setattr(
        check_module.Check,
        "command",
        mock.Mock(side_effect=(openssh_check, connection_check)),
    )
    source = mock.Mock()
    source.check.return_value = (remote_check,)
    destination = mock.Mock()
    destination.check.return_value = (local_check,)
    backup = configured_backup("home", source, destination)

    assert CheckSubcommand().main(Config({}, {"home": backup}), arguments("-q")) == 1

    remote_run.assert_not_called()
    local_run.assert_called_once_with()
    assert capsys.readouterr().out == (
        "backup: home\n"
        f"    FAIL  SSH connection works on {target}\n"
        f"          could not connect to {target}\n"
        "          connection timed out\n"
    )


def test_missing_openssh_skips_all_remote_checks(monkeypatch, tmp_path):
    target = SSHTarget("ssh://server", tmp_path / "key")
    remote_check, remote_run = deferred_result("remote check", ssh=target)
    openssh_check, openssh_run = deferred_result("OpenSSH is installed", "could not start ssh")
    command = mock.Mock(return_value=openssh_check)
    monkeypatch.setattr(check_module.Check, "command", command)
    source = mock.Mock()
    source.check.return_value = (remote_check,)
    destination = mock.Mock()
    destination.check.return_value = ()
    backup = configured_backup("home", source, destination)

    assert CheckSubcommand().main(Config({}, {"home": backup}), arguments("-q")) == 1

    command.assert_called_once_with("OpenSSH is installed", ("ssh", "-V"))
    openssh_run.assert_called_once_with()
    remote_run.assert_not_called()
