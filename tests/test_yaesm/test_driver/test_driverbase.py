"""Tests for yaesm.driver.driverbase."""

from datetime import datetime

import pytest
import voluptuous as vlp

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm.check import CheckRole
from yaesm.command import CommandRunner
from yaesm.driver.driverbase import DriverBase, DriverError
from yaesm.representation import (
    BlockDevice,
    ByteStream,
    CompressedStream,
    DataProperty,
    EncryptedStream,
    ReadableTree,
    Representation,
)


class AllCapabilitiesDriver(DriverBase):
    @classmethod
    def name(cls) -> str:
        return "source-store"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})

    def cap_source(self) -> Representation:
        return Representation()

    def cap_store(
        self,
        source: Representation,
        operation: bckp.BackupOperation,
        base: Representation | None = None,
    ) -> bckp.BackupArtifact:
        assert base is not None
        return bckp.BackupArtifact(operation, source)

    def cap_snapshot(self, source: Representation) -> Representation:
        return source

    def cap_expose(self, source: BlockDevice) -> ReadableTree:
        return ReadableTree()

    def cap_export(self, source: Representation, base: Representation | None = None) -> ByteStream:
        return ByteStream()

    def cap_import(
        self,
        source: ByteStream,
        operation: bckp.BackupOperation,
        base: Representation | None = None,
    ) -> bckp.BackupArtifact:
        assert base is not None
        return bckp.BackupArtifact(operation, source)

    def cap_compress(self, source: ByteStream) -> CompressedStream:
        return CompressedStream()

    def cap_encrypt(self, source: ByteStream) -> EncryptedStream:
        return EncryptedStream()

    def cap_list(
        self,
        backup_name: str,
    ) -> ty.Sequence[bckp.BackupArtifact[Representation]]:
        return ()

    def cap_delete(
        self,
        artifacts: ty.Sequence[bckp.BackupArtifact[Representation]],
    ) -> None:
        pass

    def cap_cleanup(self, representation: Representation) -> None:
        pass

    def cap_fake(self) -> None:
        pass


class EmptyDriver(DriverBase):
    @classmethod
    def name(cls) -> str:
        return "empty"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})


class DriverWithoutConfiguration(DriverBase):
    @classmethod
    def name(cls) -> str:
        return "unconfigured"


class DriverWithoutCustomChecks(DriverBase):
    @classmethod
    def name(cls) -> str:
        return "unchecked"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})


class DriverWithDifferentExecutable(EmptyDriver):
    @classmethod
    def name(cls) -> str:
        return "different"

    @classmethod
    def executable_name(cls) -> str:
        return "actual"


class DriverWithDifferentExecutableCheck(EmptyDriver):
    @classmethod
    def executable_check_command(cls) -> tuple[str, ...]:
        return ("special", "version")


def test_driver_has_global_settings():
    settings = {"setting": "value"}

    assert EmptyDriver(global_settings=settings).global_settings is settings
    assert EmptyDriver().global_settings == {}


def test_capabilities_are_advertised_by_defining_methods():
    assert AllCapabilitiesDriver.capabilities() == {
        "source",
        "store",
        "snapshot",
        "expose",
        "export",
        "import",
        "compress",
        "encrypt",
        "list",
        "delete",
        "cleanup",
    }


def test_lifecycle_capabilities_are_excluded_from_pipelines():
    assert {"list", "delete", "cleanup"}.isdisjoint(AllCapabilitiesDriver.pipeline_capabilities())


def test_driver_without_capabilities_advertises_none():
    assert EmptyDriver.capabilities() == set()


def test_unmarked_method_is_not_a_capability():
    assert "fake" not in AllCapabilitiesDriver.capabilities()


def test_check_is_not_a_capability():
    assert "check" not in AllCapabilitiesDriver.capabilities()


@pytest.mark.parametrize("role", CheckRole)
def test_driver_check_accepts_each_role(role):
    assert tuple(check.description for check in EmptyDriver().check(role)) == (
        "empty is installed",
    )


def test_capability_method_is_found_from_metadata():
    driver = AllCapabilitiesDriver()
    assert driver.capability_method("source") == driver.cap_source


def test_capability_effects_are_described_by_metadata():
    driver = AllCapabilitiesDriver()
    assert driver.capability_metadata("snapshot").adds == {DataProperty.SNAPSHOT}
    assert driver.capability_metadata("compress").adds == {DataProperty.COMPRESSED}
    assert driver.capability_metadata("encrypt").adds == {DataProperty.ENCRYPTED}
    assert driver.capability_metadata("export").base == "source"
    assert driver.capability_metadata("store").base == "destination"
    assert driver.capability_metadata("import").base == "destination"
    assert driver.capability_metadata("snapshot").temporary is True


@pytest.mark.parametrize(
    "capability",
    [
        "source",
        "store",
        "snapshot",
        "expose",
        "export",
        "import",
        "compress",
        "encrypt",
        "list",
        "delete",
        "cleanup",
    ],
)
def test_unsupported_capability_error_names_driver(capability):
    driver = EmptyDriver()
    method = driver.capability_method(capability)
    operation = bckp.BackupOperation("name", "manual", datetime(2000, 1, 1))
    if capability == "source":
        args = ()
    elif capability == "list":
        args = ("name",)
    elif capability == "delete":
        args = ((),)
    elif capability in {"store", "import"}:
        args = (Representation(), operation)
    else:
        args = (Representation(),)
    with pytest.raises(
        NotImplementedError, match=f"empty driver does not provide the {capability} capability"
    ):
        method(*args)


def test_driver_configuration_schema_is_required():
    with pytest.raises(TypeError):
        DriverWithoutConfiguration()


def test_driver_check_is_inherited():
    assert len(DriverWithoutCustomChecks().check(CheckRole.SOURCE)) == 1


def test_stored_artifact_check_is_deferred(monkeypatch):
    driver = AllCapabilitiesDriver()
    calls = []
    artifact = bckp.BackupArtifact(
        bckp.BackupOperation("home", "daily", datetime(2026, 8, 28)),
        Representation(),
    )
    monkeypatch.setattr(
        driver,
        "cap_list",
        lambda backup_name: calls.append(backup_name) or (artifact,),
    )

    check = driver.check_artifacts("home")

    assert calls == []
    assert check.run().passed
    assert calls == ["home"]


def test_stored_artifact_check_requires_an_artifact():
    result = AllCapabilitiesDriver().check_artifacts("home").run()

    assert result.failure == "no stored artifacts found for backup 'home'"


def test_stored_artifact_check_reports_listing_failure(monkeypatch):
    driver = AllCapabilitiesDriver()

    def fail(_backup_name):
        raise DriverError("listing failed")

    monkeypatch.setattr(driver, "cap_list", fail)

    result = driver.check_artifacts("home").run()

    assert result.failure == "listing failed"


def test_driver_configuration_schema():
    assert EmptyDriver.config_schema()({}) == {}


def test_driver_has_command_runner():
    assert isinstance(EmptyDriver().runner, CommandRunner)


def test_executable_defaults_to_driver_name():
    assert EmptyDriver.executable_name() == "empty"
    assert EmptyDriver.executable_check_command() == ("empty", "--version")


def test_executable_name_can_differ_from_driver_name():
    assert DriverWithDifferentExecutable.executable_name() == "actual"
    assert DriverWithDifferentExecutable.executable_check_command() == ("actual", "--version")


def test_executable_check_command_can_be_overridden():
    assert DriverWithDifferentExecutableCheck.executable_check_command() == (
        "special",
        "version",
    )


def test_unknown_capability_method_is_rejected():
    with pytest.raises(ValueError, match="unknown capability: missing"):
        EmptyDriver().capability_method("missing")


def test_incremental_bases_are_accepted():
    driver = AllCapabilitiesDriver()
    operation = bckp.BackupOperation("name", "manual", datetime(2000, 1, 1))
    representation = Representation()
    stream = ByteStream()

    assert isinstance(driver.cap_export(representation, representation), ByteStream)
    assert (
        driver.cap_store(representation, operation, representation).representation is representation
    )
    assert driver.cap_import(stream, operation, representation).representation is stream
