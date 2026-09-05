"""Plain file source and destination driver."""

import dataclasses
from pathlib import Path
from uuid import uuid4

import voluptuous as vlp

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm.check import Check, CheckRole
from yaesm.command import CommandStage
from yaesm.driver.driverbase import DriverBase, DriverError, GlobalSettings
from yaesm.errors import YaesmValueError
from yaesm.representation import CommandStream, PathTree
from yaesm.ssh import SSHTarget, command_for_ssh, same_endpoint


class FileDriverError(DriverError):
    """Raised when a file capability cannot be performed."""


@dataclasses.dataclass(frozen=True, init=False)
class FileStream(CommandStream):
    """A byte stream read from a local or remote file."""

    path: ty.Path
    ssh: SSHTarget | None

    def __init__(
        self,
        path: ty.Path,
        ssh: SSHTarget | None = None,
        *,
        suffixes: ty.Sequence[str] = (),
    ) -> None:
        path = Path(path)
        object.__setattr__(self, "stages", (CommandStage(("cat", path), ssh),))
        object.__setattr__(self, "suffixes", tuple(suffixes))
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "ssh", ssh)


def _operation_and_suffixes(
    backup_name: str,
    filename: str,
) -> tuple[bckp.BackupOperation, tuple[str, ...]]:
    suffixes: tuple[str, ...] = ()
    while True:
        try:
            return bckp.BackupOperation.from_artifact_name(backup_name, filename), suffixes
        except YaesmValueError:
            filename, separator, suffix = filename.rpartition(".")
            if not separator:
                raise
            suffixes = (f".{suffix}", *suffixes)


class FileDriver(DriverBase):
    """Read an existing file or store a byte stream as a file."""

    def __init__(
        self,
        location: ty.Path,
        ssh: SSHTarget | None = None,
        *,
        global_settings: GlobalSettings | None = None,
    ) -> None:
        super().__init__(global_settings, ssh=ssh)
        self.location = Path(location)

    @classmethod
    def name(cls) -> str:
        return "file"

    @classmethod
    def executable_check_command(cls) -> None:
        return None

    @staticmethod
    def config_schema() -> vlp.Schema:
        def absolute_path(value: object) -> ty.Path:
            if not isinstance(value, str | Path):
                raise vlp.Invalid("location must be a path")
            path = Path(value)
            if not path.is_absolute():
                raise vlp.Invalid("location must be an absolute path")
            return path

        return vlp.Schema(lambda value: {"location": absolute_path(value)})

    def _checks(self, role: CheckRole) -> tuple[Check, ...]:
        if role is CheckRole.SOURCE:
            checks = (
                ("file exists", ("test", "-f", self.location)),
                ("file is readable", ("test", "-r", self.location)),
            )
        elif role in {CheckRole.ARTIFACT_SOURCE, CheckRole.DESTINATION}:
            checks = (
                ("directory exists", ("test", "-d", self.location)),
                ("directory is readable", ("test", "-r", self.location)),
                *(
                    (("directory is writable", ("test", "-w", self.location)),)
                    if role is CheckRole.DESTINATION
                    else ()
                ),
                ("directory is searchable", ("test", "-x", self.location)),
            )
        else:
            return ()
        return tuple(
            self._command_check(f"{description}: {self.location}", command)
            for description, command in checks
        )

    def artifact_roots(self) -> tuple[PathTree, ...]:
        return (PathTree(self.location, self.ssh),)

    def cap_source(self) -> FileStream:
        return FileStream(self.location, self.ssh, suffixes=self.location.suffixes)

    def cap_import(
        self,
        source: CommandStream,
        operation: bckp.BackupOperation,
        base: FileStream | None = None,
    ) -> bckp.BackupArtifact[FileStream]:
        artifact_name = operation.artifact_name + "".join(source.suffixes)
        destination = self.location / artifact_name
        temporary = self.location / f".{artifact_name}.tmp-{uuid4().hex}"
        try:
            self.runner.pipeline(
                (
                    *source.stages,
                    CommandStage(("dd", f"of={temporary}", "bs=1048576"), self.ssh),
                )
            )
            self.runner.run(command_for_ssh(self.ssh, ("mv", temporary, destination)))
        except BaseException:
            self._delete((temporary,), check=False)
            raise
        return bckp.BackupArtifact(
            operation,
            FileStream(destination, self.ssh, suffixes=source.suffixes),
        )

    def cap_list(self, backup_name: str) -> tuple[bckp.BackupArtifact[FileStream], ...]:
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
                operation, suffixes = _operation_and_suffixes(backup_name, path.name)
            except YaesmValueError:
                continue
            artifacts.append(
                bckp.BackupArtifact(
                    operation,
                    FileStream(path, self.ssh, suffixes=suffixes),
                )
            )
        return tuple(
            sorted(artifacts, key=lambda artifact: artifact.operation.instant, reverse=True)
        )

    def format_locator(self, artifact: bckp.BackupArtifact[FileStream]) -> str:
        file = artifact.representation
        return str(file.path) if file.ssh is None else file.ssh.format_location(file.path)

    def cap_delete(
        self,
        artifacts: ty.Sequence[bckp.BackupArtifact[FileStream]],
    ) -> None:
        files = tuple(artifact.representation for artifact in artifacts)
        if any(not same_endpoint(file.ssh, self.ssh) for file in files):
            raise FileDriverError("file uses a different OpenSSH endpoint")
        self._delete(tuple(file.path for file in files))

    def _delete(self, paths: ty.Sequence[ty.Path], *, check: bool = True) -> None:
        if not paths:
            return
        self.runner.run(
            command_for_ssh(self.ssh, ("rm", "-f", *paths)),
            check=check,
        )
