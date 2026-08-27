"""Tests for yaesm.pipeline."""

from datetime import datetime

import pytest
import voluptuous as vlp

import yaesm.ty as ty
from yaesm.backup import BackupArtifact, BackupOperation
from yaesm.command import CommandError
from yaesm.driver.driverbase import DriverBase, DriverError, capability
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
    def __init__(self) -> None:
        self.output = ReadableTree()

    @classmethod
    def name(cls) -> str:
        return "source"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})

    def cap_source(self) -> ReadableTree:
        return self.output


class ExportDriver(DriverBase):
    def __init__(self) -> None:
        self.output = UncompressedStream()
        self.call: tuple[Representation, Representation | None] | None = None

    @classmethod
    def name(cls) -> str:
        return "export"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})

    def cap_export(
        self, source: Representation, base: Representation | None = None
    ) -> UncompressedStream:
        self.call = (source, base)
        return self.output


class FailingExportDriver(ExportDriver):
    def cap_export(
        self,
        source: Representation,
        base: Representation | None = None,
    ) -> UncompressedStream:
        raise CommandError(("export",), 1, "failed")


class SnapshotDriver(DriverBase):
    def __init__(self, cleanups: list[str] | None = None) -> None:
        self.output = ReadableTree()
        self.cleanups = [] if cleanups is None else cleanups

    @classmethod
    def name(cls) -> str:
        return "snapshot"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})

    def cap_snapshot(self, source: Representation) -> ReadableTree:
        return self.output

    def cap_cleanup(self, representation: Representation) -> None:
        self.cleanups.append(self.name())


class SnapshotWithoutCleanupDriver(SnapshotDriver):
    cap_cleanup = DriverBase.cap_cleanup


class TemporaryExportDriver(ExportDriver):
    def __init__(self, cleanups: list[str]) -> None:
        super().__init__()
        self.cleanups = cleanups

    @capability("export", base="source", temporary=True)
    def cap_export(
        self,
        source: Representation,
        base: Representation | None = None,
    ) -> UncompressedStream:
        return super().cap_export(source, base)

    def cap_cleanup(self, representation: Representation) -> None:
        self.cleanups.append(self.name())


class DestinationDriver(DriverBase):
    def __init__(self) -> None:
        self.call: tuple[ByteStream, BackupOperation, Representation | None] | None = None

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
        self.call = (source, operation, base)
        return BackupArtifact(operation, source)


class BrokenDestinationDriver(DestinationDriver):
    def cap_import(
        self,
        source: ByteStream,
        operation: BackupOperation,
        base: Representation | None = None,
    ) -> BackupArtifact:
        return ty.cast(BackupArtifact, ReadableTree())


class FailingDestinationDriver(DestinationDriver):
    def cap_import(
        self,
        source: ByteStream,
        operation: BackupOperation,
        base: Representation | None = None,
    ) -> BackupArtifact:
        raise CommandError(("import",), 1, "failed")


class FailingCleanupDriver(SnapshotDriver):
    def cap_cleanup(self, representation: Representation) -> None:
        raise DriverError("cleanup failed")


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


def test_incremental_base_can_contain_only_destination_state():
    destination = ReadableTree()

    base = IncrementalBase(None, destination, datetime(2026, 8, 27, 12, 30))

    assert base.source is None
    assert base.destination is destination


def test_pipeline_executes_resolved_capabilities():
    source = SourceDriver()
    exporter = ExportDriver()
    destination = DestinationDriver()
    operation = BackupOperation("home", "hourly", datetime(2026, 8, 27, 12, 30))

    artifact = Pipeline(source, destination, (exporter,)).execute(operation)

    assert exporter.call == (source.output, None)
    assert destination.call == (exporter.output, operation, None)
    assert artifact == BackupArtifact(operation, exporter.output)


def test_pipeline_passes_each_side_of_incremental_base():
    source = SourceDriver()
    exporter = ExportDriver()
    destination = DestinationDriver()
    source_base = ReadableTree()
    destination_base = ByteStream()
    base = IncrementalBase(
        source_base,
        destination_base,
        datetime(2026, 8, 27, 12, 0),
    )
    operation = BackupOperation("home", "hourly", datetime(2026, 8, 27, 13, 0))

    Pipeline(source, destination, (exporter,)).execute(operation, base)

    assert exporter.call == (source.output, source_base)
    assert destination.call == (exporter.output, operation, destination_base)


def test_pipeline_executes_transform_capabilities():
    operation = BackupOperation("home", "hourly", datetime(2026, 8, 27, 13, 0))
    pipeline = Pipeline(
        SourceDriver(),
        DestinationDriver(),
        (ExportDriver(), CompressionDriver(), CompressedEncryptionDriver()),
        requirements={DataProperty.ENCRYPTED},
    )

    artifact = pipeline.execute(operation)

    assert isinstance(artifact.representation, EncryptedStream)


def test_pipeline_execution_error_names_backup_and_capability():
    pipeline = Pipeline(SourceDriver(), BrokenDestinationDriver(), (ExportDriver(),))
    operation = BackupOperation("home", "hourly", datetime(2026, 8, 27, 13, 0))

    with pytest.raises(PipelineError) as error:
        pipeline.execute(operation)

    assert str(error.value) == (
        "backup 'home': destination.import did not produce a backup artifact"
    )


def test_pipeline_formats_capability_failure_with_context():
    pipeline = Pipeline(SourceDriver(), DestinationDriver(), (FailingExportDriver(),))
    operation = BackupOperation("home", "hourly", datetime(2026, 8, 27, 13, 0))

    with pytest.raises(PipelineError) as error:
        pipeline.execute(operation)

    assert error.value.format() == (
        "backup 'home' failed in export.export\n  command exited with status 1: export\n    failed"
    )


def test_pipeline_cleans_up_temporary_representations():
    snapshotter = SnapshotDriver()
    pipeline = Pipeline(
        SourceDriver(),
        DestinationDriver(),
        (snapshotter, ExportDriver()),
        requirements={DataProperty.SNAPSHOT},
    )

    pipeline.execute(BackupOperation("home", "hourly", datetime(2026, 8, 27, 13, 0)))

    assert snapshotter.cleanups == ["snapshot"]


def test_pipeline_cleans_up_in_reverse_order_after_failure():
    cleanups: list[str] = []
    pipeline = Pipeline(
        SourceDriver(),
        FailingDestinationDriver(),
        (SnapshotDriver(cleanups), TemporaryExportDriver(cleanups)),
        requirements={DataProperty.SNAPSHOT},
    )

    with pytest.raises(PipelineError, match="failed in destination.import"):
        pipeline.execute(BackupOperation("home", "hourly", datetime(2026, 8, 27, 13, 0)))

    assert cleanups == ["export", "snapshot"]


def test_pipeline_formats_cleanup_failure_with_context():
    pipeline = Pipeline(
        SourceDriver(),
        DestinationDriver(),
        (FailingCleanupDriver(), ExportDriver()),
        requirements={DataProperty.SNAPSHOT},
    )

    with pytest.raises(PipelineError) as error:
        pipeline.execute(BackupOperation("home", "hourly", datetime(2026, 8, 27, 13, 0)))

    assert error.value.format() == (
        "backup 'home' failed while cleaning up snapshot.snapshot\n  cleanup failed"
    )


def test_pipeline_rejects_temporary_capability_without_cleanup():
    with pytest.raises(PipelineError, match="driver provides no cleanup capability"):
        Pipeline(
            SourceDriver(),
            DestinationDriver(),
            (SnapshotWithoutCleanupDriver(), ExportDriver()),
            requirements={DataProperty.SNAPSHOT},
        )


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
    assert pipeline.source is source
    assert pipeline.destination is destination


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
