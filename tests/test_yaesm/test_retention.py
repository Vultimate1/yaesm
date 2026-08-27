"""Tests for yaesm.retention."""

from datetime import datetime

import pytest

from yaesm.backup import BackupArtifact, BackupOperation
from yaesm.representation import Representation
from yaesm.retention import KeepLast


def artifact(schedule_name: str, day: int) -> BackupArtifact:
    operation = BackupOperation("home", schedule_name, datetime(2026, 8, day))
    return BackupArtifact(operation, Representation())


def test_keep_last():
    older = artifact("hourly", 1)
    newer = artifact("hourly", 3)
    newest = artifact("hourly", 5)

    assert KeepLast(2).retain([newer, older, newest], datetime(2026, 8, 6)) == [newest, newer]


def test_keep_last_filters_by_schedule():
    hourly = artifact("hourly", 1)
    daily = artifact("daily", 2)

    assert KeepLast(1, "hourly").retain([daily, hourly], datetime(2026, 8, 3)) == [hourly]


def test_keep_last_rejects_invalid_count():
    with pytest.raises(ValueError, match="count must be at least 1"):
        KeepLast(0)
