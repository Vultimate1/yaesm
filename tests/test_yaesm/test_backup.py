"""Tests for yaesm.backup."""

from datetime import datetime

import pytest

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm.pipeline import Pipeline
from yaesm.representation import Representation
from yaesm.retention import KeepLast
from yaesm.schedule import Schedule


@pytest.fixture
def pipeline() -> Pipeline:
    return ty.cast(Pipeline, object())


def test_backup_has_composable_settings(pipeline):
    schedules = (Schedule("hourly", ()),)
    retention_policies = (KeepLast(1),)
    backup = bckp.Backup("home", pipeline, schedules, retention_policies)

    assert vars(backup) == {
        "name": "home",
        "pipeline": pipeline,
        "schedules": schedules,
        "retention_policies": retention_policies,
    }


def test_backup_operation():
    created_at = datetime(2026, 8, 27, 12, 30)
    operation = bckp.BackupOperation(
        backup_name="home",
        schedule_name="hourly",
        created_at=created_at,
    )

    assert operation.backup_name == "home"
    assert operation.schedule_name == "hourly"
    assert operation.created_at == created_at
    assert operation.artifact_name == "yaesm-home-hourly.2026_08_27_12:30"


def test_backup_operation_from_artifact_name():
    assert bckp.BackupOperation.from_artifact_name(
        "home-backup",
        "yaesm-home-backup-every.six-hours.2026_08_27_12:30",
    ) == bckp.BackupOperation(
        "home-backup",
        "every.six-hours",
        datetime(2026, 8, 27, 12, 30),
    )


@pytest.mark.parametrize(
    "artifact_name",
    [
        "other-home-hourly.2026_08_27_12:30",
        "yaesm-other-hourly.2026_08_27_12:30",
        "yaesm-home-.2026_08_27_12:30",
        "yaesm-home-hourly",
        "yaesm-home-hourly.invalid",
        "yaesm-home-hourly.2026_08_27_12:30.extra",
    ],
)
def test_backup_operation_rejects_invalid_artifact_name(artifact_name):
    with pytest.raises(ValueError, match="invalid artifact name"):
        bckp.BackupOperation.from_artifact_name("home", artifact_name)


def test_backup_artifact():
    created_at = datetime(2026, 8, 22, 12, 30)
    operation = bckp.BackupOperation(
        backup_name="home",
        schedule_name="hourly",
        created_at=created_at,
    )
    representation = Representation()
    artifact = bckp.BackupArtifact(operation, representation)

    assert artifact.name == "yaesm-home-hourly.2026_08_22_12:30"
    assert artifact.operation is operation
    assert artifact.representation is representation


@pytest.mark.parametrize(
    "name",
    [
        "f",
        "F",
        "foo",
        "Foo",
        "FOO",
        "foO",
        "foo12",
        "foo_12",
        "foo_12_",
        "foo-12",
        "foo-12_",
        "foo-12-",
        "FOO-12-",
        "FOO@BAR",
        "FOO@12-",
        "FOO@@--12-:",
        "F@-_:",
    ],
)
def test_backup_accepts_valid_name(name, pipeline):
    assert bckp.Backup(name, pipeline, (), ()).name == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        " ",
        " foo",
        "foo ",
        " foo ",
        "foo bar",
        "1",
        "0foo",
        "foo/bar",
        "foo,bar",
        "foo*bar",
        "@foo",
        "-foo",
        ":foo",
        "f^oo",
    ],
)
def test_backup_rejects_invalid_name(name, pipeline):
    with pytest.raises(ValueError, match="invalid backup name"):
        bckp.Backup(name, pipeline, (), ())
