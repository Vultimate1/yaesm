"""Rsync driver and representations."""

import dataclasses
import hashlib
import shlex
from pathlib import Path

import voluptuous as vlp

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm.check import Check, CheckRole
from yaesm.command import Command
from yaesm.driver.driverbase import DriverBase, DriverError, GlobalSettings
from yaesm.errors import YaesmValueError
from yaesm.representation import PathTree, Representation
from yaesm.ssh import SSHTarget, command_for_ssh, same_endpoint


class RsyncDriverError(DriverError):
    """Raised when an Rsync capability cannot be performed."""


@dataclasses.dataclass(frozen=True)
class RsyncTree(PathTree):
    """A directory tree stored by rsync."""


class RsyncDriver(DriverBase):
    """Provide rsync backup capabilities for a configured location."""

    def __init__(
        self,
        location: ty.Path,
        ssh: SSHTarget | None = None,
        extra_options: ty.Sequence[str] = (),
        exclude: ty.Sequence[str] = (),
        one_file_system: bool = False,
        *,
        global_settings: GlobalSettings | None = None,
    ) -> None:
        super().__init__(global_settings, ssh=ssh)
        if isinstance(extra_options, str) or any(
            not isinstance(option, str) or not option for option in extra_options
        ):
            raise YaesmValueError("extra_options must contain nonempty strings")
        if isinstance(exclude, str) or any(
            not isinstance(pattern, str) or not pattern for pattern in exclude
        ):
            raise YaesmValueError("exclude must contain nonempty strings")
        if not isinstance(one_file_system, bool):
            raise YaesmValueError("one_file_system must be a boolean")
        self.location = Path(location)
        self.extra_options = tuple(extra_options)
        self.exclude = tuple(exclude)
        self.one_file_system = one_file_system

    @classmethod
    def name(cls) -> str:
        return "rsync"

    @staticmethod
    def _marker_prefix() -> str:
        return ".yaesm-rsync-artifact-"

    @staticmethod
    def _marker(path: ty.Path) -> ty.Path:
        digest = hashlib.sha256(path.name.encode()).hexdigest()
        return path.with_name(f"{RsyncDriver._marker_prefix()}{digest}")

    @staticmethod
    def config_schema() -> vlp.Schema:
        def absolute_path(value: object) -> ty.Path:
            if not isinstance(value, str | Path):
                raise vlp.Invalid("location must be a path")
            path = Path(value)
            if not path.is_absolute():
                raise vlp.Invalid("location must be an absolute path")
            return path

        def extra_options(value: object) -> tuple[str, ...]:
            if isinstance(value, str):
                values = (value,)
            elif isinstance(value, list | tuple):
                values_list = []
                for option in value:
                    if not isinstance(option, str):
                        raise vlp.Invalid("extra_options must be a string or list/tuple of strings")
                    values_list.append(option)
                values = tuple(values_list)
            else:
                raise vlp.Invalid("extra_options must be a string or list/tuple of strings")
            try:
                return tuple(word for option in values for word in shlex.split(option))
            except ValueError as error:
                raise vlp.Invalid(f"invalid extra_options: {error}") from error

        def exclude(value: object) -> tuple[str, ...]:
            values = (value,) if isinstance(value, str) else value
            if not isinstance(values, list | tuple) or any(
                not isinstance(pattern, str) or not pattern for pattern in values
            ):
                raise vlp.Invalid("exclude must be a string or list/tuple of nonempty strings")
            return tuple(values)

        def one_file_system(value: object) -> bool:
            if not isinstance(value, bool):
                raise vlp.Invalid("one_file_system must be a boolean")
            return value

        return vlp.Schema(
            {
                vlp.Required("location"): absolute_path,
                vlp.Optional("extra_options", default=()): extra_options,
                vlp.Optional("exclude", default=()): exclude,
                vlp.Optional("one_file_system", default=False): one_file_system,
            }
        )

    def _checks(self, role: CheckRole) -> tuple[Check, ...]:
        match role:
            case CheckRole.SOURCE | CheckRole.ARTIFACT_SOURCE:
                requirements = (
                    ("directory exists", ("test", "-d", self.location)),
                    ("directory is readable", ("test", "-r", self.location)),
                    ("directory is searchable", ("test", "-x", self.location)),
                )
            case CheckRole.DESTINATION:
                requirements = (
                    ("directory exists", ("test", "-d", self.location)),
                    ("directory is readable", ("test", "-r", self.location)),
                    ("directory is writable", ("test", "-w", self.location)),
                    ("directory is searchable", ("test", "-x", self.location)),
                )
            case CheckRole.TRANSFORM:
                return ()
        return tuple(
            self._command_check(
                f"{description}: {self.location}",
                command,
            )
            for description, command in requirements
        )

    def _base_compatible(
        self,
        capability: str,
        source: Representation,
        source_base: Representation | None,
        destination_base: Representation | None,
    ) -> bool:
        return (
            capability == "store"
            and isinstance(destination_base, RsyncTree)
            and same_endpoint(destination_base.ssh, self.ssh)
        )

    def cap_source(self) -> PathTree:
        return PathTree(self.location, self.ssh)

    def cap_store(
        self,
        source: PathTree,
        operation: bckp.BackupOperation,
        base: RsyncTree | None = None,
    ) -> bckp.BackupArtifact[RsyncTree]:
        if base is not None and not same_endpoint(base.ssh, self.ssh):
            raise RsyncDriverError("rsync base and destination use different SSH endpoints")

        destination = RsyncTree(self.location / operation.artifact_name, self.ssh)
        command: list[str | ty.Path] = [
            "rsync",
            "--archive",
            "--hard-links",
            "--acls",
            "--xattrs",
            "--sparse",
            *(("--one-file-system",) if self.one_file_system else ()),
            "--numeric-ids",
            "--delete",
            "--delete-excluded",
            "--protect-args",
            *(f"--exclude={pattern}" for pattern in self.exclude),
            *self.extra_options,
        ]
        if base is not None:
            command.append(f"--link-dest={base.path}")
        command.extend((_directory(source.path), _directory(destination.path)))
        rsync_command = self._command(source.ssh, destination.ssh, command)

        try:
            self.runner.run(rsync_command)
            self.runner.run(
                command_for_ssh(
                    self.ssh,
                    ("touch", self._marker(destination.path)),
                )
            )
        except BaseException:
            self._delete((destination,), check=False)
            raise
        return bckp.BackupArtifact(operation, destination)

    def cap_list(self, backup_name: str) -> tuple[bckp.BackupArtifact[RsyncTree], ...]:
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
                    "(",
                    "-type",
                    "d",
                    "-o",
                    "-type",
                    "f",
                    "-name",
                    f"{self._marker_prefix()}*",
                    ")",
                    "-print",
                ),
            ),
            capture_output=True,
        )
        paths = {Path(value) for value in (result.stdout or "").splitlines()}
        artifacts = []
        for path in paths:
            if self._marker(path) not in paths:
                continue
            try:
                operation = bckp.BackupOperation.from_artifact_name(backup_name, path.name)
            except YaesmValueError:
                continue
            artifacts.append(bckp.BackupArtifact(operation, RsyncTree(path, self.ssh)))
        return tuple(
            sorted(artifacts, key=lambda artifact: artifact.operation.created_at, reverse=True)
        )

    def format_locator(self, artifact: bckp.BackupArtifact[RsyncTree]) -> str:
        tree = artifact.representation
        return str(tree.path) if tree.ssh is None else tree.ssh.format_location(tree.path)

    def cap_delete(
        self,
        artifacts: ty.Sequence[bckp.BackupArtifact[RsyncTree]],
    ) -> None:
        trees = tuple(artifact.representation for artifact in artifacts)
        if any(not same_endpoint(tree.ssh, self.ssh) for tree in trees):
            raise RsyncDriverError("rsync artifact uses a different SSH endpoint")
        self._delete(trees)

    def _command(
        self,
        source: SSHTarget | None,
        destination: SSHTarget | None,
        command: Command,
    ) -> tuple[str, ...]:
        if same_endpoint(source, destination):
            return command_for_ssh(destination, command)
        if source is not None and destination is not None:
            raise RsyncDriverError("rsync cannot copy between different SSH endpoints")

        remote = source if source is not None else destination
        assert remote is not None
        command = list(command)
        command.insert(-2, f"--rsh={shlex.join(('ssh', *remote.openssh_options()))}")
        if source is not None:
            command[-2] = _remote_directory(source, Path(command[-2]))
        else:
            assert destination is not None
            command[-1] = _remote_directory(destination, Path(command[-1]))
        return tuple(str(argument) for argument in command)

    def _delete(self, trees: ty.Sequence[RsyncTree], *, check: bool = True) -> None:
        if not trees:
            return
        paths = tuple(path for tree in trees for path in (tree.path, self._marker(tree.path)))
        self.runner.run(
            command_for_ssh(
                trees[0].ssh,
                ("rm", "-rf", *paths),
            ),
            check=check,
        )


def _directory(path: ty.Path) -> str:
    value = str(path)
    return value if value == "/" else f"{value.rstrip('/')}/"


def _remote_directory(ssh: SSHTarget, path: ty.Path) -> str:
    host = f"[{ssh.host}]" if ":" in ssh.host else ssh.host
    destination = host if ssh.user is None else f"{ssh.user}@{host}"
    return f"{destination}:{_directory(path)}"
