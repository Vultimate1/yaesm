"""Tests for yaesm.backup."""

from datetime import datetime

import pytest
import voluptuous as vlp

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm.driver.driverbase import DriverBase, DriverError
from yaesm.errors import YaesmValueError
from yaesm.representation import ByteStream, CommandStream, Representation
from yaesm.retention import KeepLast
from yaesm.schedule import CronSchedule, Schedule


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
        super().__init__()
        self.artifacts = tuple(artifacts)
        self.failure = failure
        self.base: Representation | None = None
        self.source: Representation | None = None
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
        self.source = source
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


class ArtifactDriver(DestinationDriver):
    def __init__(self, artifacts: ty.Sequence[bckp.BackupArtifact] = ()) -> None:
        super().__init__(artifacts)
        self.export_call: tuple[Representation, Representation | None] | None = None

    def cap_export(
        self,
        source: Representation,
        base: Representation | None = None,
    ) -> CommandStream:
        self.export_call = (source, base)
        return CommandStream()


class StreamDestinationDriver(DriverBase):
    def __init__(self, artifacts: ty.Sequence[bckp.BackupArtifact] = ()) -> None:
        super().__init__()
        self.artifacts = tuple(artifacts)
        self.call: tuple[ByteStream, bckp.BackupOperation, Representation | None] | None = None

    @classmethod
    def name(cls) -> str:
        return "stream-destination"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})

    def cap_import(
        self,
        source: ByteStream,
        operation: bckp.BackupOperation,
        base: Representation | None = None,
    ) -> bckp.BackupArtifact:
        self.call = (source, operation, base)
        return bckp.BackupArtifact(operation, source)

    def cap_list(self, backup_name: str) -> ty.Sequence[bckp.BackupArtifact]:
        return self.artifacts


def artifact(
    schedule_name: str,
    hour: int,
    backup_name: str = "home",
    representation: Representation | None = None,
) -> bckp.BackupArtifact:
    operation = bckp.BackupOperation(
        backup_name,
        schedule_name,
        datetime(2026, 8, 27, hour),
    )
    return bckp.BackupArtifact(
        operation, Representation() if representation is None else representation
    )


def configured_backup(
    destination: DestinationDriver,
    retention_policies: tuple[KeepLast, ...] = (),
) -> bckp.Backup:
    return bckp.Backup(
        "home",
        bckp.DriverSource(SourceDriver()),
        destination,
        retention_policies=retention_policies,
    )


def test_backup_has_composable_settings():
    source = bckp.DriverSource(SourceDriver())
    destination = DestinationDriver()
    drivers = (ArtifactDriver(),)
    schedules = (Schedule("hourly", CronSchedule("0 * * * *")),)
    retention_policies = (KeepLast(1),)
    backup = bckp.Backup(
        "home",
        source,
        destination,
        drivers,
        frozenset(),
        schedules,
        retention_policies,
    )

    assert vars(backup) == {
        "name": "home",
        "source": source,
        "destination": destination,
        "drivers": drivers,
        "requirements": frozenset(),
        "schedules": schedules,
        "retention_policies": retention_policies,
    }


def test_driver_source_identifies_live_driver():
    driver = SourceDriver()

    assert bckp.DriverSource(driver).driver is driver


def test_backup_source_identifies_configured_backup():
    assert bckp.BackupSource("local-home").backup_name == "local-home"


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
def test_backup_accepts_valid_name(name):
    assert bckp.Backup(name, bckp.DriverSource(SourceDriver()), DestinationDriver()).name == name


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
def test_backup_rejects_invalid_name(name):
    with pytest.raises(YaesmValueError, match="invalid backup name"):
        bckp.Backup(name, bckp.DriverSource(SourceDriver()), DestinationDriver())


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


def test_backup_execute_replicates_newest_artifact_with_matching_bases():
    current_source = artifact("hourly", 12, "local")
    previous_source = artifact("hourly", 11, "local")
    source_driver = ArtifactDriver((current_source, previous_source))
    source_backup = bckp.Backup(
        "local",
        bckp.DriverSource(SourceDriver()),
        source_driver,
    )
    previous_destination = artifact("daily", 11, "offsite", ByteStream())
    destination = StreamDestinationDriver((previous_destination,))
    backup = bckp.Backup("offsite", bckp.BackupSource("local"), destination)

    result = backup.execute(
        "daily",
        datetime(2026, 8, 27, 13),
        {"local": source_backup},
    )

    assert result.operation == bckp.BackupOperation(
        "offsite",
        "daily",
        current_source.operation.created_at,
    )
    assert source_driver.export_call == (
        current_source.representation,
        previous_source.representation,
    )
    assert destination.call == (
        CommandStream(),
        result.operation,
        previous_destination.representation,
    )


def test_backup_execute_does_not_replicate_same_artifact_twice():
    source_artifact = artifact("hourly", 12, "local")
    source_driver = ArtifactDriver((source_artifact,))
    source_backup = bckp.Backup(
        "local",
        bckp.DriverSource(SourceDriver()),
        source_driver,
    )
    existing = artifact("daily", 12, "offsite", ByteStream())
    destination = StreamDestinationDriver((existing,))
    backup = bckp.Backup("offsite", bckp.BackupSource("local"), destination)

    result = backup.execute(
        "daily",
        datetime(2026, 8, 27, 13),
        {"local": source_backup},
    )

    assert result is existing
    assert source_driver.export_call is None
    assert destination.call is None


def test_backup_execute_rejects_unknown_source_backup():
    backup = bckp.Backup(
        "offsite",
        bckp.BackupSource("missing"),
        StreamDestinationDriver(),
    )

    with pytest.raises(
        bckp.BackupError,
        match="backup 'offsite' references unknown source backup 'missing'",
    ):
        backup.execute("daily", datetime(2026, 8, 27, 13), {})


def test_backup_execute_rejects_source_backup_without_artifacts():
    source_backup = bckp.Backup(
        "local",
        bckp.DriverSource(SourceDriver()),
        ArtifactDriver(),
    )
    backup = bckp.Backup(
        "offsite",
        bckp.BackupSource("local"),
        StreamDestinationDriver(),
    )

    with pytest.raises(
        bckp.BackupError,
        match="backup 'offsite' cannot run: source backup 'local' has no artifacts",
    ):
        backup.execute("daily", datetime(2026, 8, 27, 13), {"local": source_backup})


def test_backup_execute_formats_source_listing_failure():
    source_backup = bckp.Backup(
        "local",
        bckp.DriverSource(SourceDriver()),
        DestinationDriver(failure="list"),
    )
    backup = bckp.Backup(
        "offsite",
        bckp.BackupSource("local"),
        StreamDestinationDriver(),
    )

    with pytest.raises(bckp.BackupError) as error:
        backup.execute("daily", datetime(2026, 8, 27, 13), {"local": source_backup})

    assert error.value.format() == (
        "backup 'offsite' failed while listing source backup 'local' artifacts\n  list failed"
    )


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
