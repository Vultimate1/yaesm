"""Tests for yaesm.pipeline."""

import logging
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest
import voluptuous as vlp

import yaesm.command as command_module
import yaesm.ty as ty
from yaesm.backup import BackupArtifact, BackupOperation
from yaesm.command import CommandError, CommandResult, CommandStage
from yaesm.config import parse_config
from yaesm.driver.btrfsdriver import BtrfsDriver
from yaesm.driver.driverbase import DriverBase, DriverError, capability
from yaesm.driver.gpgdriver import GPGDriver
from yaesm.driver.tardriver import TarDriver
from yaesm.driver.zstddriver import ZstdDriver
from yaesm.pipeline import IncrementalBase, Pipeline, PipelineError, PipelineStep
from yaesm.representation import (
    ByteStream,
    CommandStream,
    CompressedStream,
    DataProperty,
    EncryptedStream,
    ReadableTree,
    Representation,
)


class SourceDriver(DriverBase):
    def __init__(self) -> None:
        super().__init__()
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
        super().__init__()
        self.output = CommandStream()
        self.call: tuple[Representation, Representation | None] | None = None

    @classmethod
    def name(cls) -> str:
        return "export"

    @staticmethod
    def config_schema() -> vlp.Schema:
        return vlp.Schema({})

    def cap_export(
        self, source: Representation, base: Representation | None = None
    ) -> CommandStream:
        self.call = (source, base)
        return self.output

    def _base_compatible(
        self,
        capability: str,
        source: Representation,
        source_base: Representation | None,
        destination_base: Representation | None,
    ) -> bool:
        return True


class RejectingExportDriver(ExportDriver):
    def _base_compatible(
        self,
        capability: str,
        source: Representation,
        source_base: Representation | None,
        destination_base: Representation | None,
    ) -> bool:
        return False


class FailingExportDriver(ExportDriver):
    def cap_export(
        self,
        source: Representation,
        base: Representation | None = None,
    ) -> CommandStream:
        raise CommandError(("export",), 1, "failed")


class SnapshotDriver(DriverBase):
    def __init__(self, cleanups: list[str] | None = None) -> None:
        super().__init__()
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
    ) -> CommandStream:
        return super().cap_export(source, base)

    def cap_cleanup(self, representation: Representation) -> None:
        self.cleanups.append(self.name())


class DestinationDriver(DriverBase):
    def __init__(self) -> None:
        super().__init__()
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
    ) -> BackupArtifact[ByteStream]:
        self.call = (source, operation, base)
        return BackupArtifact(operation, source)

    def _base_compatible(
        self,
        capability: str,
        source: Representation,
        source_base: Representation | None,
        destination_base: Representation | None,
    ) -> bool:
        return True


class BrokenDestinationDriver(DestinationDriver):
    def cap_import(
        self,
        source: ByteStream,
        operation: BackupOperation,
        base: Representation | None = None,
    ) -> BackupArtifact[ByteStream]:
        return ty.cast(BackupArtifact, ReadableTree())


class FailingDestinationDriver(DestinationDriver):
    def cap_import(
        self,
        source: ByteStream,
        operation: BackupOperation,
        base: Representation | None = None,
    ) -> BackupArtifact[ByteStream]:
        raise CommandError(("import",), 1, "failed")


class CompressionRequiredDestinationDriver(DestinationDriver):
    @capability("import", requires=(DataProperty.COMPRESSED,))
    def cap_import(
        self,
        source: ByteStream,
        operation: BackupOperation,
        base: Representation | None = None,
    ) -> BackupArtifact[ByteStream]:
        return BackupArtifact(operation, source)


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

    def cap_compress(self, source: ByteStream) -> CompressedStream:
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


def test_pipeline_logs_capability_steps(caplog):
    pipeline = Pipeline(SourceDriver(), DestinationDriver(), (ExportDriver(),))
    operation = BackupOperation("home", "hourly", datetime(2026, 8, 27, 12, 30))

    with caplog.at_level(logging.INFO, logger="yaesm.pipeline"):
        pipeline.execute(operation)

    assert caplog.messages == [
        "backup 'home': source.source",
        "backup 'home': export.export",
        "backup 'home': destination.import",
    ]


def test_pipeline_executes_from_existing_artifact():
    source_operation = BackupOperation("local", "hourly", datetime(2026, 8, 27, 12))
    source = BackupArtifact(source_operation, ReadableTree())
    source_base = ReadableTree()
    destination_base = ByteStream()
    base = IncrementalBase(source_base, destination_base, source_operation.created_at)
    operation = BackupOperation("offsite", "daily", datetime(2026, 8, 27, 13))
    exporter = ExportDriver()
    destination = DestinationDriver()
    pipeline = Pipeline(exporter, destination, source_artifact=source)

    artifact = pipeline.execute(operation, base)

    assert pipeline.steps == (
        PipelineStep(exporter, "export"),
        PipelineStep(destination, "import"),
    )
    assert exporter.call == (source.representation, source_base)
    assert destination.call == (exporter.output, operation, destination_base)
    assert artifact == BackupArtifact(operation, exporter.output)


def test_pipeline_uses_source_driver_only_when_needed_for_existing_artifact():
    source = BackupArtifact(
        BackupOperation("local", "hourly", datetime(2026, 8, 27, 12)),
        ReadableTree(),
    )
    exporter = ExportDriver()
    destination = DestinationDriver()

    needed = Pipeline(exporter, destination, source_artifact=source)
    skipped = Pipeline(
        exporter,
        destination,
        source_artifact=BackupArtifact(source.operation, ByteStream()),
    )

    assert needed.steps == (
        PipelineStep(exporter, "export"),
        PipelineStep(destination, "import"),
    )
    assert skipped.steps == (PipelineStep(destination, "import"),)


def test_pipeline_validates_replication_without_existing_artifact():
    Pipeline.validate_replication(
        BtrfsDriver(Path("/source")),
        BtrfsDriver(Path("/destination")),
    )


def test_pipeline_rejects_incompatible_replication_without_existing_artifact():
    with pytest.raises(PipelineError) as error:
        Pipeline.validate_replication(
            TarDriver(Path("/archives")),
            BtrfsDriver(Path("/destination")),
        )

    assert "produced: TarArchive" in str(error.value)
    assert "destination accepts: BtrfsStream via btrfs.import" in str(error.value)


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


def test_pipeline_omits_unapproved_incremental_base():
    source = SourceDriver()
    exporter = RejectingExportDriver()
    destination = DestinationDriver()
    base = IncrementalBase(
        ReadableTree(),
        ByteStream(),
        datetime(2026, 8, 27, 12, 0),
    )

    Pipeline(source, destination, (exporter,)).execute(
        BackupOperation("home", "hourly", datetime(2026, 8, 27, 13, 0)),
        base,
    )

    assert exporter.call == (source.output, None)
    assert destination.call is not None
    assert destination.call[2] is None


def test_pipeline_executes_transform_capabilities():
    operation = BackupOperation("home", "hourly", datetime(2026, 8, 27, 13, 0))
    pipeline = Pipeline(
        SourceDriver(),
        DestinationDriver(),
        (ExportDriver(), CompressionDriver(), CompressedEncryptionDriver()),
    )

    artifact = pipeline.execute(operation)

    assert isinstance(artifact.representation, EncryptedStream)


def test_configured_remote_pipeline_uses_one_ssh_command():
    config = parse_config(
        {
            "home": {
                "ssh": {
                    "endpoint": "ssh://server",
                    "identity_file": "/key",
                },
                "source": {"btrfs": "/source", "remote": True},
                "transforms": [
                    {"zstd": {}, "remote": True},
                    {"gpg": "/public-key.asc", "remote": True},
                ],
                "destination": {"tar": "/backups", "remote": True},
                "schedules": {
                    "manual": {
                        "on-demand": {},
                        "retention": {"keep-last": 1},
                    }
                },
            }
        }
    )
    backup = config.backups["home"]
    assert isinstance(backup.source, BtrfsDriver)
    assert isinstance(backup.destination, TarDriver)
    assert isinstance(backup.transforms[0], ZstdDriver)
    assert isinstance(backup.transforms[1], GPGDriver)
    ssh = backup.source.ssh
    assert ssh is not None

    backup.source.runner = mock.Mock()
    backup.source.runner.run.side_effect = lambda _command, **options: CommandResult(
        (
            "UUID: 11111111-1111-1111-1111-111111111111\nParent UUID: -\nReceived UUID: -\n"
            if options.get("capture_output")
            else None
        ),
        "",
        (0,),
    )
    submitted = []

    def pipeline(commands, **_options):
        submitted.append(tuple(commands))
        return CommandResult(None, "", (0,))

    backup.destination.runner = mock.Mock()
    backup.destination.runner.pipeline.side_effect = pipeline
    backup.destination.runner.run.return_value = CommandResult(None, "", (0,))

    Pipeline(backup.source, backup.destination, backup.transforms).execute(
        BackupOperation("home", "manual", datetime(2026, 8, 29, 12))
    )

    assert len(submitted) == 1
    stages = submitted[0]
    assert all(isinstance(stage, CommandStage) and stage.ssh is ssh for stage in stages)
    assert tuple(stage.command[0] for stage in stages if isinstance(stage, CommandStage)) == (
        "tar",
        "zstd",
        "gpg",
        "dd",
    )
    assert command_module._execution_commands(stages) == (
        ssh.openssh_pipeline(
            tuple(stage.command for stage in stages if isinstance(stage, CommandStage))
        ),
    )


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
    )

    pipeline.execute(BackupOperation("home", "hourly", datetime(2026, 8, 27, 13, 0)))

    assert snapshotter.cleanups == ["snapshot"]


def test_pipeline_cleans_up_in_reverse_order_after_failure():
    cleanups: list[str] = []
    pipeline = Pipeline(
        SourceDriver(),
        FailingDestinationDriver(),
        (SnapshotDriver(cleanups), TemporaryExportDriver(cleanups)),
    )

    with pytest.raises(PipelineError, match="failed in destination.import"):
        pipeline.execute(BackupOperation("home", "hourly", datetime(2026, 8, 27, 13, 0)))

    assert cleanups == ["export", "snapshot"]


def test_pipeline_formats_cleanup_failure_with_context():
    pipeline = Pipeline(
        SourceDriver(),
        DestinationDriver(),
        (FailingCleanupDriver(), ExportDriver()),
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
    assert pipeline.source_driver is source
    assert pipeline.source_artifact is None
    assert pipeline.destination is destination


def test_pipeline_automatically_snapshots_snapshot_capable_source():
    source = BtrfsDriver(Path("/source"))
    destination = TarDriver(Path("/destination"))

    assert Pipeline(source, destination).steps == (
        PipelineStep(source, "source"),
        PipelineStep(source, "snapshot"),
        PipelineStep(destination, "export"),
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
        "  destination accepts: ByteStream via destination.import"
    )


def test_pipeline_explains_incompatible_existing_artifact():
    source = BackupArtifact(
        BackupOperation("local", "hourly", datetime(2026, 8, 27, 12)),
        ReadableTree(),
    )

    with pytest.raises(PipelineError) as error:
        Pipeline(EmptyDriver(), DestinationDriver(), source_artifact=source)

    assert str(error.value) == (
        "cannot build backup pipeline:\n"
        "  last usable route: existing artifact\n"
        "  produced: ReadableTree\n"
        "  available properties: none\n"
        "  destination accepts: ByteStream via destination.import"
    )


def test_pipeline_uses_every_configured_driver():
    source = SourceDriver()
    exporter = ExportDriver()
    encryption = EncryptionDriver()
    destination = DestinationDriver()
    pipeline = Pipeline(
        source,
        destination,
        (exporter, encryption),
    )

    assert pipeline.steps == (
        PipelineStep(source, "source"),
        PipelineStep(exporter, "export"),
        PipelineStep(encryption, "encrypt"),
        PipelineStep(destination, "import"),
    )


def test_pipeline_rejects_unused_driver():
    with pytest.raises(PipelineError) as error:
        Pipeline(
            SourceDriver(),
            DestinationDriver(),
            (ExportDriver(), EmptyDriver()),
        )

    assert str(error.value) == (
        "cannot build backup pipeline:\n"
        "  compatible route: source.source -> export.export -> destination.import\n"
        "  next configured transform cannot be used: empty"
    )


def test_pipeline_explains_when_required_representation_cannot_be_stored():
    with pytest.raises(PipelineError) as error:
        Pipeline(
            BtrfsDriver(Path("/source")),
            BtrfsDriver(Path("/destination")),
            (GPGDriver(Path("/public-key.asc")),),
        )

    assert str(error.value) == (
        "cannot build backup pipeline:\n"
        "  last usable route: btrfs.source -> btrfs.snapshot -> "
        "btrfs.export -> gpg.encrypt\n"
        "  produced: GPGStream\n"
        "  available properties: encrypted, snapshot\n"
        "  destination accepts: BtrfsStream via btrfs.import, "
        "BtrfsSubvolume via btrfs.store"
    )


def test_pipeline_reports_the_storage_route_with_fewest_missing_properties():
    with pytest.raises(PipelineError) as error:
        Pipeline(
            SourceDriver(),
            CompressionRequiredDestinationDriver(),
            (ExportDriver(), EncryptionDriver()),
        )

    assert str(error.value) == (
        "cannot build backup pipeline:\n"
        "  compatible route: source.source -> export.export -> encryption.encrypt -> "
        "destination.import\n"
        "  missing required properties: compressed"
    )


def test_pipeline_preserves_configured_driver_order():
    source = SourceDriver()
    exporter = ExportDriver()
    compression = CompressionDriver()
    encryption = EncryptionDriver()
    destination = DestinationDriver()
    pipeline = Pipeline(
        source,
        destination,
        (exporter, encryption, compression),
    )

    assert pipeline.steps == (
        PipelineStep(source, "source"),
        PipelineStep(exporter, "export"),
        PipelineStep(encryption, "encrypt"),
        PipelineStep(compression, "compress"),
        PipelineStep(destination, "import"),
    )


def test_pipeline_rejects_incompatible_configured_driver_order():
    with pytest.raises(PipelineError) as error:
        Pipeline(
            SourceDriver(),
            DestinationDriver(),
            (ExportDriver(), CompressedEncryptionDriver(), CompressionDriver()),
        )

    assert str(error.value) == (
        "cannot build backup pipeline:\n"
        "  compatible route: source.source -> export.export -> destination.import\n"
        "  next configured transform cannot be used: encryption"
    )
