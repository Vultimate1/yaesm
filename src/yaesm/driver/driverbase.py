"""Base class for composable backup drivers."""

import abc
import dataclasses

import voluptuous as vlp

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm.check import Check, CheckResult, CheckRole
from yaesm.command import Command, CommandResult, CommandRunner
from yaesm.errors import YaesmError
from yaesm.representation import (
    BlockDevice,
    ByteStream,
    CompressedStream,
    DataProperty,
    EncryptedStream,
    ReadableTree,
    Representation,
)
from yaesm.ssh import SSHTarget

_CAPABILITY_ATTRIBUTE = "__yaesm_capability__"
_Method = ty.TypeVar("_Method", bound=ty.Callable[..., object])
GlobalSettings: ty.TypeAlias = ty.Mapping[str, object]


class DriverError(bckp.BackupError):
    """Raised when a driver cannot perform a capability."""


@dataclasses.dataclass(frozen=True)
class CapabilityMetadata:
    """Metadata attached to a capability method."""

    name: str
    adds: frozenset[DataProperty] = frozenset()
    requires: frozenset[DataProperty] = frozenset()
    base: ty.Literal["source", "destination"] | None = None
    pipeline: bool = True
    temporary: bool = False


def capability(
    name: str,
    *,
    adds: ty.Sequence[DataProperty] = (),
    requires: ty.Sequence[DataProperty] = (),
    base: ty.Literal["source", "destination"] | None = None,
    pipeline: bool = True,
    temporary: bool = False,
) -> ty.Callable[[_Method], _Method]:
    """Mark a DriverBase method as a capability."""

    def decorate(method: _Method) -> _Method:
        setattr(
            method,
            _CAPABILITY_ATTRIBUTE,
            CapabilityMetadata(
                name,
                frozenset(adds),
                frozenset(requires),
                base,
                pipeline,
                temporary,
            ),
        )
        return method

    return decorate


class DriverBase(abc.ABC):
    """Base class for drivers that provide backup capabilities.

    Drivers advertise a capability by overriding its corresponding marked method.

    The available capabilities are:

    - ``cap_source``: make source data available for a backup.
    - ``cap_store``: persist backup data as an artifact.
    - ``cap_snapshot``: create a point-in-time representation.
    - ``cap_expose``: expose a block device as a readable tree.
    - ``cap_export``: export a representation as a byte stream.
    - ``cap_import``: import a byte stream as a stored artifact.
    - ``cap_compress``: compress a byte stream.
    - ``cap_encrypt``: encrypt a byte stream.
    - ``cap_list``: list stored backup artifacts.
    - ``cap_delete``: delete stored backup artifacts.
    - ``cap_cleanup``: remove a temporary representation.
    """

    def __init__(self, global_settings: GlobalSettings | None = None) -> None:
        self.global_settings = {} if global_settings is None else global_settings
        self.runner = CommandRunner()

    @classmethod
    @abc.abstractmethod
    def name(cls) -> str:
        """Return the stable name used to select this driver."""

    @classmethod
    def executable_name(cls) -> str:
        """Return the primary executable, overriding this when it differs from ``name``."""
        return cls.name()

    @classmethod
    def executable_check_command(cls) -> tuple[str, ...]:
        """Return a harmless command that verifies the primary executable can run."""
        return (cls.executable_name(), "--version")

    @classmethod
    @ty.final
    def capabilities(cls) -> frozenset[str]:
        """Return the capabilities implemented by this driver."""
        return frozenset(
            metadata.name
            for method_name, method in vars(DriverBase).items()
            if (metadata := getattr(method, _CAPABILITY_ATTRIBUTE, None)) is not None
            if getattr(cls, method_name) is not method
        )

    @classmethod
    @ty.final
    def pipeline_capabilities(cls) -> frozenset[str]:
        """Return capabilities that can participate in a creation pipeline."""
        return frozenset(
            metadata.name
            for method_name, method in vars(DriverBase).items()
            if (metadata := getattr(method, _CAPABILITY_ATTRIBUTE, None)) is not None
            if metadata.pipeline
            if getattr(cls, method_name) is not method
        )

    @ty.final
    def capability_method(self, name: str) -> ty.Callable[..., object]:
        """Return the method associated with a capability name."""
        for method_name, method in vars(DriverBase).items():
            metadata = getattr(method, _CAPABILITY_ATTRIBUTE, None)
            if metadata is not None and metadata.name == name:
                return getattr(self, method_name)
        raise ValueError(f"unknown capability: {name}")

    def capability_metadata(self, name: str) -> CapabilityMetadata:
        """Return the metadata associated with a capability name."""
        for method_name, method in vars(DriverBase).items():
            metadata = getattr(method, _CAPABILITY_ATTRIBUTE, None)
            if metadata is not None and metadata.name == name:
                implementation = getattr(type(self), method_name)
                return getattr(implementation, _CAPABILITY_ATTRIBUTE, metadata)
        raise ValueError(f"unknown capability: {name}")

    @staticmethod
    @abc.abstractmethod
    def config_schema() -> vlp.Schema:
        """Return the complete schema for this driver's configuration."""

    @ty.final
    def check(self, role: CheckRole) -> tuple[Check, ...]:
        """Return deferred, read-only feasibility checks for this driver."""
        executable = self.executable_name()
        return (
            self._command_check(
                f"{executable} is installed",
                self.executable_check_command(),
            ),
            *self._checks(role),
        )

    @ty.final
    def check_artifacts(self, backup_name: str) -> Check:
        """Return a check that stored artifacts exist for a backup."""
        description = f"stored artifacts exist for backup {backup_name!r}"

        def run() -> CheckResult:
            try:
                artifacts = self.cap_list(backup_name)
            except YaesmError as error:
                return CheckResult(description, error.format())
            failure = None if artifacts else f"no stored artifacts found for backup {backup_name!r}"
            return CheckResult(description, failure)

        return Check(description, run, self._check_target())

    def _checks(self, role: CheckRole) -> tuple[Check, ...]:
        """Return this driver's additional feasibility checks."""
        return ()

    def _check_target(self) -> SSHTarget | None:
        """Return the SSH target on which this driver's checks run."""
        return None

    def _command_check(
        self,
        description: str,
        command: Command,
        *,
        validate: ty.Callable[[CommandResult], str | None] | None = None,
    ) -> Check:
        """Return a deferred command check."""
        return Check.command(
            description,
            command,
            target=self._check_target(),
            validate=validate,
        )

    @capability("source")
    def cap_source(self) -> Representation:
        """Make source data available in a supported representation."""
        raise NotImplementedError(f"{self.name()} driver does not provide the source capability")

    @capability("store", base="destination")
    def cap_store(
        self,
        source: Representation,
        operation: bckp.BackupOperation,
        base: Representation | None = None,
    ) -> bckp.BackupArtifact:
        """Persist source as an artifact for an operation, optionally using a base."""
        raise NotImplementedError(f"{self.name()} driver does not provide the store capability")

    @capability("snapshot", adds=(DataProperty.SNAPSHOT,), temporary=True)
    def cap_snapshot(self, source: Representation) -> Representation:
        """Create a point-in-time representation of source data."""
        raise NotImplementedError(f"{self.name()} driver does not provide the snapshot capability")

    @capability("expose")
    def cap_expose(self, source: BlockDevice) -> ReadableTree:
        """Expose a block device as a readable directory tree."""
        raise NotImplementedError(f"{self.name()} driver does not provide the expose capability")

    @capability("export", base="source")
    def cap_export(self, source: Representation, base: Representation | None = None) -> ByteStream:
        """Export source data, optionally relative to another representation."""
        raise NotImplementedError(f"{self.name()} driver does not provide the export capability")

    @capability("import", base="destination")
    def cap_import(
        self,
        source: ByteStream,
        operation: bckp.BackupOperation,
        base: Representation | None = None,
    ) -> bckp.BackupArtifact:
        """Import a stream as an artifact for an operation, optionally using a base."""
        raise NotImplementedError(f"{self.name()} driver does not provide the import capability")

    @capability("compress", adds=(DataProperty.COMPRESSED,))
    def cap_compress(self, source: ByteStream) -> CompressedStream:
        """Compress a byte stream."""
        raise NotImplementedError(f"{self.name()} driver does not provide the compress capability")

    @capability("encrypt", adds=(DataProperty.ENCRYPTED,))
    def cap_encrypt(self, source: ByteStream) -> EncryptedStream:
        """Encrypt a byte stream."""
        raise NotImplementedError(f"{self.name()} driver does not provide the encrypt capability")

    @capability("list", pipeline=False)
    def cap_list(
        self,
        backup_name: str,
    ) -> ty.Sequence[bckp.BackupArtifact[Representation]]:
        """Return stored artifacts for a backup, newest first."""
        raise NotImplementedError(f"{self.name()} driver does not provide the list capability")

    @capability("delete", pipeline=False)
    def cap_delete(
        self,
        artifacts: ty.Sequence[bckp.BackupArtifact[Representation]],
    ) -> None:
        """Delete stored backup artifacts."""
        raise NotImplementedError(f"{self.name()} driver does not provide the delete capability")

    @capability("cleanup", pipeline=False)
    def cap_cleanup(self, representation: Representation) -> None:
        """Remove a temporary representation produced by this driver."""
        raise NotImplementedError(f"{self.name()} driver does not provide the cleanup capability")
