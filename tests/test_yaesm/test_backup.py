"""Tests for yaesm.backup."""

from datetime import datetime

import pytest
import voluptuous as vlp

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm.driver.driverbase import DriverBase, DriverError
from yaesm.errors import YaesmValueError
from yaesm.pipeline import Pipeline
from yaesm.representation import Representation
from yaesm.retention import KeepLast
from yaesm.schedule import CronSchedule, Schedule


@pytest.fixture
def pipeline() -> Pipeline:
    return ty.cast(Pipeline, object())


class SourceDriver(DriverBase):
    @classmethod
    def name(cls) -> str:
        return "source"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})

    def cap_source(self) -> Representation:
        return Representation()


class DestinationDriver(DriverBase):
    def __init__(
        self,
        artifacts: ty.Sequence[bckp.BackupArtifact] = (),
        failure: str | None = None,
    ) -> None:
        self.artifacts = tuple(artifacts)
        self.failure = failure
        self.base: Representation | None = None
        self.deleted: tuple[bckp.BackupArtifact, ...] | None = None

    @classmethod
    def name(cls) -> str:
        return "destination"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})

    def cap_store(
        self,
        source: Representation,
        operation: bckp.BackupOperation,
        base: Representation | None = None,
    ) -> bckp.BackupArtifact:
        self.base = base
        return bckp.BackupArtifact(operation, Representation())

    def cap_list(self, backup_name: str) -> ty.Sequence[bckp.BackupArtifact]:
        if self.failure == "list":
            raise DriverError("list failed")
        return self.artifacts

    def cap_delete(self, artifacts: ty.Sequence[bckp.BackupArtifact]) -> None:
        if self.failure == "delete":
            raise DriverError("delete failed")
        self.deleted = tuple(artifacts)


def artifact(schedule_name: str, hour: int) -> bckp.BackupArtifact:
    operation = bckp.BackupOperation(
        "home",
        schedule_name,
        datetime(2026, 8, 27, hour),
    )
    return bckp.BackupArtifact(operation, Representation())


def configured_backup(
    destination: DestinationDriver,
    retention_policies: tuple[KeepLast, ...] = (),
) -> bckp.Backup:
    return bckp.Backup(
        "home",
        Pipeline(SourceDriver(), destination),
        (),
        retention_policies,
    )


def test_backup_has_composable_settings(pipeline):
    schedules = (Schedule("hourly", CronSchedule("0 * * * *")),)
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
    with pytest.raises(YaesmValueError, match="invalid artifact name"):
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
    with pytest.raises(YaesmValueError, match="invalid backup name"):
        bckp.Backup(name, pipeline, (), ())


def test_backup_execute_uses_newest_artifact_as_base_and_applies_retention():
    newest = artifact("hourly", 11)
    older = artifact("hourly", 10)
    destination = DestinationDriver((newest, older))

    result = configured_backup(destination, (KeepLast(2),)).execute(
        "hourly",
        datetime(2026, 8, 27, 12),
    )

    assert result.operation == bckp.BackupOperation(
        "home",
        "hourly",
        datetime(2026, 8, 27, 12),
    )
    assert destination.base is newest.representation
    assert destination.deleted == (older,)


def test_backup_execute_combines_retention_policies():
    hourly = artifact("hourly", 11)
    daily = artifact("daily", 10)
    older_hourly = artifact("hourly", 9)
    older_daily = artifact("daily", 8)
    destination = DestinationDriver((hourly, daily, older_hourly, older_daily))

    configured_backup(
        destination,
        (KeepLast(1, "hourly"), KeepLast(1, "daily")),
    ).execute("hourly", datetime(2026, 8, 27, 12))

    assert destination.deleted == (hourly, older_hourly, older_daily)


def test_backup_execute_without_retention_does_not_delete():
    newest = artifact("hourly", 11)
    destination = DestinationDriver((newest,))

    configured_backup(destination).execute("hourly", datetime(2026, 8, 27, 12))

    assert destination.base is newest.representation
    assert destination.deleted is None


def test_backup_execute_formats_listing_failure():
    with pytest.raises(bckp.BackupError) as error:
        configured_backup(DestinationDriver(failure="list")).execute(
            "hourly",
            datetime(2026, 8, 27, 12),
        )

    assert error.value.format() == "backup 'home' failed while listing artifacts\n  list failed"


def test_backup_execute_formats_deletion_failure():
    destination = DestinationDriver((artifact("hourly", 11),), failure="delete")

    with pytest.raises(bckp.BackupError) as error:
        configured_backup(destination, (KeepLast(1),)).execute(
            "hourly",
            datetime(2026, 8, 27, 12),
        )

    assert error.value.format() == (
        "backup 'home' failed while deleting expired artifacts\n  delete failed"
    )
