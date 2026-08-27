"""Tests for yaesm.driver.driverbase."""

from datetime import datetime

import pytest
import voluptuous as vlp

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm.driver.driverbase import DriverBase
from yaesm.representation import (
    BlockDevice,
    ByteStream,
    CompressedStream,
    DataProperty,
    EncryptedStream,
    ReadableTree,
    Representation,
    UncompressedStream,
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

    def cap_compress(self, source: UncompressedStream) -> CompressedStream:
        return CompressedStream()

    def cap_encrypt(self, source: ByteStream) -> EncryptedStream:
        return EncryptedStream()

    def cap_delete(
        self,
        artifacts: ty.Sequence[bckp.BackupArtifact[Representation]],
    ) -> None:
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
        "delete",
    }


def test_lifecycle_capabilities_are_excluded_from_pipelines():
    assert "delete" not in AllCapabilitiesDriver.pipeline_capabilities()


def test_driver_without_capabilities_advertises_none():
    assert EmptyDriver.capabilities() == set()


def test_unmarked_method_is_not_a_capability():
    assert "fake" not in AllCapabilitiesDriver.capabilities()


def test_capability_method_is_found_from_metadata():
    driver = AllCapabilitiesDriver()
    assert driver.capability_method("source") == driver.cap_source


def test_capability_effects_are_described_by_metadata():
    driver = AllCapabilitiesDriver()
    assert driver.capability_metadata("snapshot").adds == {DataProperty.SNAPSHOT}
    assert driver.capability_metadata("compress").adds == {DataProperty.COMPRESSED}
    assert driver.capability_metadata("encrypt").adds == {DataProperty.ENCRYPTED}


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
        "delete",
    ],
)
def test_unsupported_capability_error_names_driver(capability):
    driver = EmptyDriver()
    method = driver.capability_method(capability)
    operation = bckp.BackupOperation("name", "manual", datetime(2000, 1, 1))
    if capability == "source":
        args = ()
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


def test_driver_configuration_schema():
    assert EmptyDriver.config_schema()({}) == {}


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
