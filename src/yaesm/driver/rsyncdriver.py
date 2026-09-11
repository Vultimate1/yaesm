"""Rsync driver and representations."""

import dataclasses
import shlex
from functools import cached_property
from pathlib import Path
from uuid import uuid4

import voluptuous as vlp

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm.check import Check, CheckRole
from yaesm.command import Command
from yaesm.driver.driverbase import DriverBase, DriverError, GlobalSettings
from yaesm.errors import YaesmValueError
from yaesm.representation import PathTree, Representation
from yaesm.ssh import SSHTarget, command_for_ssh, same_endpoint
from yaesm.xattr import XAttr


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

    @cached_property
    def _source_id(self) -> XAttr:
        return XAttr("yaesm.source-artifact", self.runner, self.ssh)

    def check_unchanged(self) -> tuple[Check, ...]:
        return (self._source_id.check(),)

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

        mapping = vlp.Schema(
            {
                vlp.Required("location"): absolute_path,
                vlp.Optional("extra_options", default=()): extra_options,
                vlp.Optional("exclude", default=()): exclude,
                vlp.Optional("one_file_system", default=False): one_file_system,
            }
        )
        return vlp.Schema(
            lambda value: mapping({"location": value} if isinstance(value, str | Path) else value)
        )

    def _checks(self, role: CheckRole) -> tuple[Check, ...]:
        match role:
            case CheckRole.ARTIFACT_SOURCE:
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
            case CheckRole.SOURCE | CheckRole.TRANSFORM:
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

    def artifact_roots(self) -> tuple[PathTree, ...]:
        return (PathTree(self.location, self.ssh),)

    def cap_store(
        self,
        source: PathTree,
        operation: bckp.BackupOperation,
        base: RsyncTree | None = None,
    ) -> bckp.BackupArtifact[RsyncTree]:
        if base is not None and not same_endpoint(base.ssh, self.ssh):
            raise RsyncDriverError("rsync base and destination use different SSH endpoints")
        if _can_override_protected_filters(self.extra_options):
            raise RsyncDriverError(
                "rsync extra_options could override required protected-path filters"
            )
        destination = RsyncTree(self.location / operation.artifact_name, self.ssh)
        temporary = self.location / f".{operation.artifact_name}.tmp-{uuid4().hex}"
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
            "-s",
            "--filter=-x user.yaesm.*",
            "--filter=-x yaesm.*",
            # Custom xattr filters replace rsync's default namespace filtering.
            # Protect receiver ACLs even with --delete-excluded.
            "--filter=-xsr system.*",
            *(f"--exclude={pattern}" for pattern in self.exclude),
            *(f"--exclude={self._exclude_pattern(path)}" for path in source.excluded_paths),
            *self.extra_options,
        ]
        if base is not None:
            command.append(f"--link-dest={base.path}")
        command.extend((_directory(source.path), _directory(temporary)))
        rsync_command = self._command(source.ssh, destination.ssh, command)

        self.runner.run(command_for_ssh(self.ssh, ("mkdir", temporary)))
        try:
            self.runner.run(rsync_command)
            if operation.source_artifact_id is not None:
                stored = self._source_id.write(temporary, operation.source_artifact_id)
                if operation.skip_unchanged and not stored:
                    raise RsyncDriverError(
                        f"skip_unchanged requires writable and readable extended attributes "
                        f"at {self.location}"
                    )
            self.runner.run(command_for_ssh(self.ssh, ("mv", temporary, destination.path)))
        except BaseException:
            self._delete((temporary,), check=False)
            raise
        return bckp.BackupArtifact(operation, destination)

    @staticmethod
    def _exclude_pattern(path: ty.Path) -> str:
        pattern = "".join(
            f"\\{character}" if character in "\\*?[]" else character for character in str(path)
        )
        return f"/{pattern}/"

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
                    "-type",
                    "d",
                    "-print",
                ),
            ),
            capture_output=True,
        )
        paths = {Path(value) for value in (result.stdout or "").splitlines()}
        artifacts = []
        for path in paths:
            try:
                operation = bckp.BackupOperation.from_artifact_name(backup_name, path.name)
            except YaesmValueError:
                continue
            operation = dataclasses.replace(
                operation, source_artifact_id=self._source_id.read(path) or None
            )
            artifacts.append(bckp.BackupArtifact(operation, RsyncTree(path, self.ssh)))
        return tuple(
            sorted(artifacts, key=lambda artifact: artifact.operation.instant, reverse=True)
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
        self._delete(tuple(tree.path for tree in trees))

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

    def _delete(self, paths: ty.Sequence[ty.Path], *, check: bool = True) -> None:
        if not paths:
            return
        self.runner.run(
            command_for_ssh(self.ssh, ("rm", "-rf", *paths)),
            check=check,
        )


def _directory(path: ty.Path) -> str:
    value = str(path)
    return value if value == "/" else f"{value.rstrip('/')}/"


def _remote_directory(ssh: SSHTarget, path: ty.Path) -> str:
    host = f"[{ssh.host}]" if ":" in ssh.host else ssh.host
    destination = host if ssh.user is None else f"{ssh.user}@{host}"
    return f"{destination}:{_directory(path)}"


def _can_override_protected_filters(options: ty.Sequence[str]) -> bool:
    long_options = {
        "--exclude-from",
        "--filter",
        "--include-from",
    }
    return any(
        option.partition("=")[0] in long_options
        or (option.startswith("-") and not option.startswith("--") and "f" in option[1:])
        for option in options
    )
