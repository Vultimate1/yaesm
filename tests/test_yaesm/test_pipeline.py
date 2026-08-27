"""Tests for yaesm.pipeline."""

from datetime import datetime

import pytest
import voluptuous as vlp

from yaesm.backup import BackupArtifact, BackupOperation
from yaesm.driver.driverbase import DriverBase, capability
from yaesm.pipeline import IncrementalBase, Pipeline, PipelineError, PipelineStep
from yaesm.representation import (
    ByteStream,
    CompressedStream,
    DataProperty,
    EncryptedStream,
    ReadableTree,
    Representation,
    UncompressedStream,
)


class SourceDriver(DriverBase):
    @classmethod
    def name(cls) -> str:
        return "source"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})

    def cap_source(self) -> ReadableTree:
        return ReadableTree()


class ExportDriver(DriverBase):
    @classmethod
    def name(cls) -> str:
        return "export"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})

    def cap_export(
        self, source: Representation, base: Representation | None = None
    ) -> UncompressedStream:
        return UncompressedStream()


class DestinationDriver(DriverBase):
    @classmethod
    def name(cls) -> str:
        return "destination"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})

    def cap_import(
        self,
        source: ByteStream,
        operation: BackupOperation,
        base: Representation | None = None,
    ) -> BackupArtifact:
        raise NotImplementedError


class EncryptionDriver(DriverBase):
    @classmethod
    def name(cls) -> str:
        return "encryption"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})

    def cap_encrypt(self, source: ByteStream) -> EncryptedStream:
        return EncryptedStream()


class CompressionDriver(DriverBase):
    @classmethod
    def name(cls) -> str:
        return "compression"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})

    def cap_compress(self, source: UncompressedStream) -> CompressedStream:
        return CompressedStream()


class CompressedEncryptionDriver(EncryptionDriver):
    @capability(
        "encrypt",
        adds=(DataProperty.ENCRYPTED,),
        requires=(DataProperty.COMPRESSED,),
    )
    def cap_encrypt(self, source: ByteStream) -> EncryptedStream:
        return EncryptedStream()


class EmptyDriver(DriverBase):
    @classmethod
    def name(cls) -> str:
        return "empty"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})


def test_incremental_base_pairs_source_and_destination_states():
    source = ReadableTree()
    destination = ReadableTree()
    created_at = datetime(2026, 8, 27, 12, 30)

    base = IncrementalBase(source, destination, created_at)

    assert base.source is source
    assert base.destination is destination
    assert base.created_at == created_at


def test_pipeline_resolves_compatible_driver_capabilities():
    source = SourceDriver()
    exporter = ExportDriver()
    destination = DestinationDriver()
    pipeline = Pipeline(source, destination, (exporter,))

    assert pipeline.steps == (
        PipelineStep(source, "source"),
        PipelineStep(exporter, "export"),
        PipelineStep(destination, "import"),
    )


def test_pipeline_rejects_incompatible_drivers():
    source = SourceDriver()
    destination = EmptyDriver()

    with pytest.raises(
        PipelineError, match="destination driver empty provides no storage capability"
    ):
        Pipeline(source, destination)


def test_pipeline_explains_incompatible_representations():
    with pytest.raises(PipelineError) as error:
        Pipeline(SourceDriver(), DestinationDriver())

    assert str(error.value) == (
        "cannot build backup pipeline:\n"
        "  last usable route: source.source\n"
        "  produced: ReadableTree\n"
        "  available properties: none\n"
        "  destination accepts: ByteStream"
    )


def test_pipeline_includes_required_capability():
    source = SourceDriver()
    exporter = ExportDriver()
    encryption = EncryptionDriver()
    destination = DestinationDriver()
    pipeline = Pipeline(
        source,
        destination,
        (exporter, encryption),
        requirements={DataProperty.ENCRYPTED},
    )

    assert pipeline.steps == (
        PipelineStep(source, "source"),
        PipelineStep(exporter, "export"),
        PipelineStep(encryption, "encrypt"),
        PipelineStep(destination, "import"),
    )
    assert pipeline.requirements == {DataProperty.ENCRYPTED}


def test_pipeline_rejects_unsatisfied_requirement():
    with pytest.raises(PipelineError) as error:
        Pipeline(
            SourceDriver(),
            DestinationDriver(),
            (ExportDriver(),),
            requirements={DataProperty.ENCRYPTED},
        )

    assert str(error.value) == (
        "cannot build backup pipeline:\n"
        "  compatible route: source.source -> export.export -> destination.import\n"
        "  missing required properties: encrypted"
    )


def test_pipeline_honors_capability_requirements():
    source = SourceDriver()
    exporter = ExportDriver()
    compression = CompressionDriver()
    encryption = CompressedEncryptionDriver()
    destination = DestinationDriver()
    pipeline = Pipeline(
        source,
        destination,
        (exporter, encryption, compression),
        requirements={DataProperty.ENCRYPTED},
    )

    assert pipeline.steps == (
        PipelineStep(source, "source"),
        PipelineStep(exporter, "export"),
        PipelineStep(compression, "compress"),
        PipelineStep(encryption, "encrypt"),
        PipelineStep(destination, "import"),
    )
