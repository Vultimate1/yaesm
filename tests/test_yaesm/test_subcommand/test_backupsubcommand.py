"""Tests for yaesm.subcommand.backupsubcommand."""

import argparse
from pathlib import Path
from unittest import mock

import pytest

import yaesm.subcommand.backupsubcommand as backup_module
from yaesm.config import Config
from yaesm.control import DEFAULT_CONTROL_SOCKET, ControlError
from yaesm.subcommand.backupsubcommand import BackupSubcommand


def arguments(*values: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    BackupSubcommand.add_argparser_arguments(parser)
    return parser.parse_args(values)


def test_backup_subcommand_does_not_require_local_config():
    assert not BackupSubcommand.config_required


def test_backup_arguments():
    parsed = arguments("home", "--schedule", "manual", "--control-socket", "/tmp/control")

    assert parsed.backup == "home"
    assert parsed.schedule == "manual"
    assert parsed.control_socket == Path("/tmp/control")


def test_backup_argument_defaults():
    parsed = arguments("home")

    assert parsed.schedule is None
    assert parsed.control_socket == DEFAULT_CONTROL_SOCKET


def test_backup_sends_request(monkeypatch, capsys):
    send_request = mock.Mock(
        return_value=iter(
            (
                {"type": "log", "message": "starting"},
                {"type": "result", "ok": True, "request_id": "request-id"},
            )
        )
    )
    monkeypatch.setattr(backup_module, "send_request", send_request)
    parsed = arguments("home", "--schedule", "manual")

    assert BackupSubcommand().main(Config({}, {}), parsed) == 0

    send_request.assert_called_once_with(
        DEFAULT_CONTROL_SOCKET,
        {"command": "backup", "backup": "home", "schedule": "manual"},
    )
    assert capsys.readouterr() == ("backup queued: home\n", "starting\n")


def test_backup_reports_failure(monkeypatch):
    monkeypatch.setattr(
        backup_module,
        "send_request",
        lambda _path, _request: iter(({"type": "result", "ok": False, "error": "backup failed"},)),
    )

    with pytest.raises(ControlError, match="backup failed"):
        BackupSubcommand().main(Config({}, {}), arguments("home"))


def test_backup_requires_result(monkeypatch):
    monkeypatch.setattr(
        backup_module,
        "send_request",
        lambda _path, _request: iter(()),
    )

    with pytest.raises(ControlError, match="backup request returned no result"):
        BackupSubcommand().main(Config({}, {}), arguments("home"))
