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
from yaesm.ssh import SSHTarget


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
        self.listed_names: list[str] = []
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
        self.listed_names.append(backup_name)
        return tuple(
            artifact for artifact in self.artifacts if artifact.operation.backup_name == backup_name
        )

    def cap_delete(self, artifacts: ty.Sequence[bckp.BackupArtifact]) -> None:
        if self.failure == "delete":
            raise DriverError("delete failed")
        self.deleted = tuple(artifacts)

    def _base_compatible(
        self,
        capability: str,
        source: Representation,
        source_base: Representation | None,
        destination_base: Representation | None,
    ) -> bool:
        return True


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

    def _base_compatible(
        self,
        capability: str,
        source: Representation,
        source_base: Representation | None,
        destination_base: Representation | None,
    ) -> bool:
        if capability == "export":
            return source_base is not None and destination_base is not None
        return super()._base_compatible(
            capability,
            source,
            source_base,
            destination_base,
        )


class IdentifiedArtifactDriver(ArtifactDriver):
    def artifact_id(self, artifact: bckp.BackupArtifact) -> str:
        return f"id:{artifact.stored_name}"


class StreamDestinationDriver(DriverBase):
    def __init__(self, artifacts: ty.Sequence[bckp.BackupArtifact] = ()) -> None:
        super().__init__()
        self.artifacts = tuple(artifacts)
        self.listed_names: list[str] = []
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
        self.listed_names.append(backup_name)
        return tuple(
            artifact for artifact in self.artifacts if artifact.operation.backup_name == backup_name
        )

    def _base_compatible(
        self,
        capability: str,
        source: Representation,
        source_base: Representation | None,
        destination_base: Representation | None,
    ) -> bool:
        return True


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
        SourceDriver(),
        destination,
        retention_policies=retention_policies,
    )


def test_backup_has_composable_settings():
    source = SourceDriver()
    destination = DestinationDriver()
    transforms = (ArtifactDriver(),)
    schedules = (Schedule("hourly", CronSchedule("0 * * * *")),)
    retention_policies = (KeepLast(1),)
    backup = bckp.Backup(
        "home",
        source,
        destination,
        transforms,
        schedules,
        retention_policies,
    )

    assert vars(backup) == {
        "name": "home",
        "source": source,
        "destination": destination,
        "transforms": transforms,
        "schedules": schedules,
        "retention_policies": retention_policies,
        "previous_names": (),
    }


def test_backup_rejects_multiple_ssh_configurations(tmp_path):
    source = SourceDriver(ssh=SSHTarget("ssh://first", tmp_path / "first-key"))
    driver = SourceDriver(ssh=SSHTarget("ssh://second", tmp_path / "second-key"))

    with pytest.raises(YaesmValueError, match="uses more than one SSH configuration"):
        bckp.Backup("home", source, DestinationDriver(), (driver,))


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
    assert operation.source_artifact_id is None
    assert operation.previous_backup_names == ()
    assert operation.artifact_name == "yaesm-home-hourly.2026_08_27_12:30"


def test_backup_operation_records_source_artifact():
    operation = bckp.BackupOperation(
        "offsite",
        "daily",
        datetime(2026, 8, 27, 12, 30),
        "yaesm-home-hourly.2026_08_27_12:30",
    )

    assert operation.source_artifact_id == "yaesm-home-hourly.2026_08_27_12:30"


@pytest.mark.parametrize("source_artifact_id", ["", 1])
def test_backup_operation_rejects_invalid_source_artifact_id(source_artifact_id):
    with pytest.raises(YaesmValueError, match="invalid source artifact ID"):
        bckp.BackupOperation(
            "offsite",
            "daily",
            datetime(2026, 8, 27, 12, 30),
            source_artifact_id,
        )


@pytest.mark.parametrize(
    "schedule_name",
    ["../../../outside", "daily/../../outside", "daily,weekly", "daily backup"],
)
def test_backup_operation_rejects_unsafe_schedule_name(schedule_name):
    with pytest.raises(YaesmValueError, match="invalid schedule name"):
        bckp.BackupOperation("home", schedule_name, datetime(2026, 8, 27, 12, 30))


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
    assert artifact.stored_name == artifact.name
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
    assert bckp.Backup(name, SourceDriver(), DestinationDriver()).name == name


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
        "global_settings",
        "GLOBAL_SETTINGS",
    ],
)
def test_backup_rejects_invalid_name(name):
    with pytest.raises(YaesmValueError, match="invalid backup name"):
        bckp.Backup(name, SourceDriver(), DestinationDriver())


def test_backup_accepts_previous_names():
    backup = bckp.Backup(
        "laptop-home",
        SourceDriver(),
        DestinationDriver(),
        previous_names=("home", "old-home"),
    )

    assert backup.previous_names == ("home", "old-home")
    assert backup.names == ("laptop-home", "home", "old-home")


@pytest.mark.parametrize("name", ["", "global_settings", "old/home", 1])
def test_backup_rejects_invalid_previous_name(name):
    with pytest.raises(YaesmValueError, match="invalid previous backup name"):
        bckp.Backup(
            "home",
            SourceDriver(),
            DestinationDriver(),
            previous_names=(name,),
        )


@pytest.mark.parametrize("previous_names", [("home",), ("old", "old")])
def test_backup_rejects_duplicate_name_history(previous_names):
    with pytest.raises(YaesmValueError, match="duplicate backup name"):
        bckp.Backup(
            "home",
            SourceDriver(),
            DestinationDriver(),
            previous_names=previous_names,
        )


def test_backup_rejects_schedule_name_history_collision():
    schedules = (
        Schedule(
            "nightly",
            CronSchedule("0 1 * * *"),
            previous_names=("daily",),
        ),
        Schedule("daily", CronSchedule("0 2 * * *")),
    )

    with pytest.raises(YaesmValueError, match="schedule name 'daily' is used by both"):
        bckp.Backup("home", SourceDriver(), DestinationDriver(), schedules=schedules)


def test_backup_artifacts_normalizes_previous_backup_and_schedule_names():
    current = artifact("nightly", 13, "laptop-home")
    previous_backup = artifact("nightly", 12, "home")
    previous_schedule = artifact("daily", 11, "laptop-home")
    previous_both = artifact("daily", 10, "home")
    unknown_schedule = artifact("removed", 9, "home")
    stored = (current, previous_backup, previous_schedule, previous_both, unknown_schedule)
    destination = DestinationDriver(tuple(reversed(stored)))
    backup = bckp.Backup(
        "laptop-home",
        SourceDriver(),
        destination,
        schedules=(
            Schedule(
                "nightly",
                CronSchedule("0 1 * * *"),
                previous_names=("daily",),
            ),
        ),
        previous_names=("home",),
    )

    artifacts = backup.artifacts()

    assert destination.listed_names == ["laptop-home", "home"]
    assert tuple(item.operation.schedule_name for item in artifacts) == (
        "nightly",
        "nightly",
        "nightly",
        "nightly",
        "removed",
    )
    assert all(item.operation.backup_name == "laptop-home" for item in artifacts)
    assert tuple(item.representation for item in artifacts) == tuple(
        item.representation for item in stored
    )
    assert tuple(item.stored_name for item in artifacts) == tuple(item.name for item in stored)


def test_backup_artifact_history_is_independent_of_configured_transforms():
    stored = artifact("hourly", 12)
    destination = DestinationDriver((stored,))

    before = bckp.Backup("home", SourceDriver(), destination)
    after = bckp.Backup(
        "home",
        SourceDriver(),
        destination,
        transforms=(ArtifactDriver(),),
    )

    assert before.artifacts() == (stored,)
    assert after.artifacts() == (stored,)


def test_backup_destination_change_starts_new_history():
    old_destination = DestinationDriver((artifact("hourly", 12),))
    new_destination = DestinationDriver()

    assert bckp.Backup("home", SourceDriver(), old_destination).artifacts()
    assert bckp.Backup("home", SourceDriver(), new_destination).artifacts() == ()


def test_previous_names_only_search_the_configured_destination():
    old_destination = DestinationDriver((artifact("hourly", 12, "old-home"),))
    new_destination = DestinationDriver()
    backup = bckp.Backup(
        "home",
        SourceDriver(),
        new_destination,
        previous_names=("old-home",),
    )

    assert backup.artifacts() == ()
    assert new_destination.listed_names == ["home", "old-home"]
    assert old_destination.listed_names == []


def test_backup_artifacts_rejects_duplicate_logical_operation():
    destination = DestinationDriver(
        (
            artifact("nightly", 12, "laptop-home"),
            artifact("daily", 12, "home"),
        )
    )
    backup = bckp.Backup(
        "laptop-home",
        SourceDriver(),
        destination,
        schedules=(
            Schedule(
                "nightly",
                CronSchedule("0 1 * * *"),
                previous_names=("daily",),
            ),
        ),
        previous_names=("home",),
    )

    with pytest.raises(
        bckp.BackupError,
        match=(
            "backup 'laptop-home' has multiple stored artifacts that resolve to "
            "'yaesm-laptop-home-nightly.2026_08_27_12:00'"
        ),
    ):
        backup.artifacts()


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


def test_backup_execute_uses_previous_names_for_incremental_base_and_retention():
    newest = artifact("daily", 11, "old-home")
    older = artifact("daily", 10, "old-home")
    destination = DestinationDriver((newest, older))
    backup = bckp.Backup(
        "home",
        SourceDriver(),
        destination,
        schedules=(
            Schedule(
                "nightly",
                CronSchedule("0 1 * * *"),
                previous_names=("daily",),
            ),
        ),
        retention_policies=(KeepLast(2, "nightly"),),
        previous_names=("old-home",),
    )

    result = backup.execute("nightly", datetime(2026, 8, 27, 12))

    assert destination.listed_names == ["home", "old-home"]
    assert destination.base is newest.representation
    assert result.operation.previous_backup_names == ("old-home",)
    assert destination.deleted is not None
    assert destination.deleted[0].operation.backup_name == "home"
    assert destination.deleted[0].operation.schedule_name == "nightly"
    assert destination.deleted[0].representation is older.representation


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


def test_backup_execute_rejects_existing_artifact():
    existing = artifact("hourly", 12)
    destination = DestinationDriver((existing,))

    with pytest.raises(
        bckp.BackupError,
        match="backup 'home' already has artifact 'yaesm-home-hourly.2026_08_27_12:00'",
    ):
        configured_backup(destination).execute("hourly", datetime(2026, 8, 27, 12))

    assert destination.source is None


def test_backup_execute_replicates_newest_artifact_with_matching_bases():
    current_source = artifact("hourly", 12, "local")
    previous_source = artifact("hourly", 11, "local")
    source_driver = ArtifactDriver((current_source, previous_source))
    source_backup = bckp.Backup(
        "local",
        SourceDriver(),
        source_driver,
    )
    previous_destination = bckp.BackupArtifact(
        bckp.BackupOperation(
            "offsite",
            "daily",
            previous_source.operation.created_at,
            previous_source.name,
        ),
        ByteStream(),
    )
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
        current_source.name,
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
        SourceDriver(),
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

    assert result == existing
    assert source_driver.export_call is None
    assert destination.call is None


def test_backup_execute_uses_exact_replication_base():
    wanted = artifact("hourly", 11, "local")
    same_time = artifact("daily", 11, "local")
    current = artifact("hourly", 12, "local")
    source_driver = IdentifiedArtifactDriver((current, same_time, wanted))
    source_backup = bckp.Backup(
        "local",
        SourceDriver(),
        source_driver,
    )
    previous_operation = bckp.BackupOperation(
        "offsite",
        "daily",
        wanted.operation.created_at,
        source_driver.artifact_id(wanted),
    )
    previous = bckp.BackupArtifact(previous_operation, ByteStream())
    destination = StreamDestinationDriver((previous,))
    backup = bckp.Backup("offsite", bckp.BackupSource("local"), destination)

    result = backup.execute("daily", datetime(2026, 8, 27, 13), {"local": source_backup})

    assert result.operation.source_artifact_id == source_driver.artifact_id(current)
    assert source_driver.export_call == (current.representation, wanted.representation)


def test_backup_execute_matches_replication_base_recorded_before_rename():
    previous = artifact("daily", 11, "old-local")
    current = artifact("daily", 12, "old-local")
    source_driver = IdentifiedArtifactDriver((current, previous))
    source_backup = bckp.Backup(
        "local",
        SourceDriver(),
        source_driver,
        schedules=(
            Schedule(
                "hourly",
                CronSchedule("0 * * * *"),
                previous_names=("daily",),
            ),
        ),
        previous_names=("old-local",),
    )
    previous_destination = bckp.BackupArtifact(
        bckp.BackupOperation(
            "offsite",
            "nightly",
            previous.operation.created_at,
            "id:yaesm-old-local-daily.2026_08_27_11:00",
        ),
        ByteStream(),
    )
    destination = StreamDestinationDriver((previous_destination,))
    backup = bckp.Backup("offsite", bckp.BackupSource("local"), destination)

    result = backup.execute("nightly", datetime(2026, 8, 27, 13), {"local": source_backup})

    assert source_driver.listed_names == ["local", "old-local"]
    assert result.operation.source_artifact_id == "id:yaesm-old-local-daily.2026_08_27_12:00"
    assert source_driver.export_call == (current.representation, previous.representation)


def test_backup_execute_uses_full_replication_when_base_is_missing():
    current = artifact("hourly", 12, "local")
    source_driver = ArtifactDriver((current,))
    source_backup = bckp.Backup(
        "local",
        SourceDriver(),
        source_driver,
    )
    previous_operation = bckp.BackupOperation(
        "offsite",
        "daily",
        datetime(2026, 8, 27, 11),
        "yaesm-local-hourly.2026_08_27_11:00",
    )
    previous = bckp.BackupArtifact(previous_operation, ByteStream())
    destination = StreamDestinationDriver((previous,))
    backup = bckp.Backup("offsite", bckp.BackupSource("local"), destination)

    backup.execute("daily", datetime(2026, 8, 27, 13), {"local": source_backup})

    assert source_driver.export_call == (current.representation, None)
    assert destination.call is not None
    assert destination.call[2] is None


def test_backup_execute_reuses_destination_only_base_without_source_metadata():
    current = artifact("hourly", 12, "local")
    source_driver = ArtifactDriver((current,))
    source_backup = bckp.Backup(
        "local",
        SourceDriver(),
        source_driver,
    )
    previous = artifact("daily", 11, "offsite")
    destination = DestinationDriver((previous,))
    backup = bckp.Backup("offsite", bckp.BackupSource("local"), destination)

    backup.execute("daily", datetime(2026, 8, 27, 13), {"local": source_backup})

    assert destination.base is previous.representation
    assert source_driver.export_call is None


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
        SourceDriver(),
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
        SourceDriver(),
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
