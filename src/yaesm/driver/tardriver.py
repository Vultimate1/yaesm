"""Tar archive driver."""

import dataclasses
from pathlib import Path
from uuid import uuid4

import voluptuous as vlp

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm.check import Check, CheckRole
from yaesm.command import CommandStage
from yaesm.driver.driverbase import DriverBase, DriverError, GlobalSettings, capability
from yaesm.errors import YaesmValueError
from yaesm.representation import (
    CommandStream,
    DataProperty,
    PathTree,
    Representation,
    UncompressedStream,
)
from yaesm.ssh import SSHTarget, command_for_ssh, same_endpoint


class TarDriverError(DriverError):
    """Raised when a tar capability cannot be performed."""


class TarStream(UncompressedStream):
    """An uncompressed POSIX pax archive stream."""

    suffix = ".tar"


@dataclasses.dataclass(frozen=True)
class TarArchive(Representation):
    """A stored tar archive file."""

    path: ty.Path
    ssh: SSHTarget | None = None


def _operation_from_archive_name(backup_name: str, archive_name: str) -> bckp.BackupOperation:
    while True:
        try:
            return bckp.BackupOperation.from_artifact_name(backup_name, archive_name)
        except YaesmValueError:
            archive_name, separator, _ = archive_name.rpartition(".")
            if not separator:
                raise


class TarDriver(DriverBase):
    """Create and store portable tar archives."""

    def __init__(
        self,
        location: ty.Path,
        ssh: SSHTarget | None = None,
        one_file_system: bool = True,
        *,
        global_settings: GlobalSettings | None = None,
    ) -> None:
        super().__init__(global_settings, ssh=ssh)
        if not isinstance(one_file_system, bool):
            raise YaesmValueError("one_file_system must be a boolean")
        self.location = Path(location)
        self.one_file_system = one_file_system

    @classmethod
    def name(cls) -> str:
        return "tar"

    @staticmethod
    def config_schema() -> vlp.Schema:
        def absolute_path(value: object) -> ty.Path:
            if not isinstance(value, str | Path):
                raise vlp.Invalid("location must be a path")
            path = Path(value)
            if not path.is_absolute():
                raise vlp.Invalid("location must be an absolute path")
            return path

        def one_file_system(value: object) -> bool:
            if not isinstance(value, bool):
                raise vlp.Invalid("one_file_system must be a boolean")
            return value

        mapping = vlp.Schema(
            {
                vlp.Required("location"): absolute_path,
                vlp.Optional("one_file_system"): one_file_system,
            }
        )
        return vlp.Schema(
            lambda value: mapping({"location": value} if isinstance(value, str | Path) else value)
        )

    def _checks(self, role: CheckRole) -> tuple[Check, ...]:
        if role is not CheckRole.DESTINATION:
            return ()
        return tuple(
            self._command_check(f"{description}: {self.location}", command)
            for description, command in (
                ("directory exists", ("test", "-d", self.location)),
                ("directory is readable", ("test", "-r", self.location)),
                ("directory is writable", ("test", "-w", self.location)),
                ("directory is searchable", ("test", "-x", self.location)),
            )
        )

    @capability("export", adds=(DataProperty.ARCHIVED,))
    def cap_export(self, source: PathTree, base: PathTree | None = None) -> TarStream:
        exclude = self._destination_exclude(source)
        return TarStream(
            (
                CommandStage(
                    (
                        "tar",
                        "-c",
                        "-f",
                        "-",
                        "--format=pax",
                        "--acls",
                        "--xattrs",
                        "--numeric-owner",
                        *(("--one-file-system",) if self.one_file_system else ()),
                        *((f"--exclude={exclude}",) if exclude is not None else ()),
                        "-C",
                        source.path,
                        ".",
                    ),
                    source.ssh,
                ),
            ),
            suffixes=(TarStream.suffix,),
        )

    def _destination_exclude(self, source: PathTree) -> str | None:
        if not same_endpoint(source.ssh, self.ssh):
            return None
        try:
            relative = self.location.relative_to(source.path)
        except ValueError:
            return None
        if relative == Path("."):
            raise TarDriverError(f"tar destination is also the source: {source.path}")
        pattern = "".join(
            f"\\{character}" if character in "\\*?[]" else character for character in str(relative)
        )
        return f"./{pattern}"

    @capability("import", requires=(DataProperty.ARCHIVED,))
    def cap_import(
        self,
        source: CommandStream,
        operation: bckp.BackupOperation,
        base: TarArchive | None = None,
    ) -> bckp.BackupArtifact[TarArchive]:
        artifact_name = operation.artifact_name + "".join(source.suffixes)
        destination = TarArchive(self.location / artifact_name, self.ssh)
        temporary = TarArchive(
            self.location / f".{artifact_name}.tmp-{uuid4().hex}",
            self.ssh,
        )
        try:
            self.runner.pipeline(
                (
                    *source.stages,
                    CommandStage(("dd", f"of={temporary.path}", "bs=1048576"), self.ssh),
                )
            )
            self.runner.run(
                command_for_ssh(
                    self.ssh,
                    ("mv", temporary.path, destination.path),
                )
            )
        except BaseException:
            self._delete((temporary,), check=False)
            raise
        return bckp.BackupArtifact(operation, destination)

    def cap_list(self, backup_name: str) -> tuple[bckp.BackupArtifact[TarArchive], ...]:
        result = self.runner.run(
            command_for_ssh(
                self.ssh,
                (
                    "find",
                    self.location,
                    "!",
                    "-path",
                    self.location,
                    "-prune",
                    "-type",
                    "f",
                    "-print",
                ),
            ),
            capture_output=True,
        )
        artifacts = []
        for value in (result.stdout or "").splitlines():
            path = Path(value)
            try:
                operation = _operation_from_archive_name(backup_name, path.name)
            except YaesmValueError:
                continue
            artifacts.append(bckp.BackupArtifact(operation, TarArchive(path, self.ssh)))
        return tuple(
            sorted(artifacts, key=lambda artifact: artifact.operation.instant, reverse=True)
        )

    def format_locator(self, artifact: bckp.BackupArtifact[TarArchive]) -> str:
        archive = artifact.representation
        return (
            str(archive.path) if archive.ssh is None else archive.ssh.format_location(archive.path)
        )

    def cap_delete(
        self,
        artifacts: ty.Sequence[bckp.BackupArtifact[TarArchive]],
    ) -> None:
        archives = tuple(artifact.representation for artifact in artifacts)
        if any(not same_endpoint(archive.ssh, self.ssh) for archive in archives):
            raise TarDriverError("tar archive uses a different SSH endpoint")
        self._delete(archives)

    def _delete(self, archives: ty.Sequence[TarArchive], *, check: bool = True) -> None:
        if not archives:
            return
        self.runner.run(
            command_for_ssh(
                archives[0].ssh,
                ("rm", "-f", *(archive.path for archive in archives)),
            ),
            check=check,
        )
