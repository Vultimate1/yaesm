"""Rsync driver and representations."""

import dataclasses
import shlex
from pathlib import Path

import voluptuous as vlp

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm.check import Check, CheckRole
from yaesm.command import Command, CommandRunner
from yaesm.driver.driverbase import DriverBase, DriverError
from yaesm.errors import YaesmValueError
from yaesm.representation import PathTree
from yaesm.ssh import SSHTarget, command_for_target, same_endpoint


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
        target: SSHTarget | None = None,
        extra_options: ty.Sequence[str] = (),
        runner: CommandRunner | None = None,
    ) -> None:
        if isinstance(extra_options, str) or any(
            not isinstance(option, str) or not option for option in extra_options
        ):
            raise YaesmValueError("extra_options must contain nonempty strings")
        self.location = Path(location)
        self.target = target
        self.extra_options = tuple(extra_options)
        self.runner = CommandRunner() if runner is None else runner

    @classmethod
    def name(cls) -> str:
        return "rsync"

    @staticmethod
    def config_schema() -> vlp.Schema:
        def absolute_path(value: object) -> ty.Path:
            if not isinstance(value, str | Path):
                raise vlp.Invalid("location must be a path")
            path = Path(value)
            if not path.is_absolute():
                raise vlp.Invalid("location must be an absolute path")
            return path

        def ssh_target(value: object) -> SSHTarget:
            if not isinstance(value, SSHTarget):
                raise vlp.Invalid("target must be an SSHTarget")
            return value

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

        return vlp.Schema(
            {
                vlp.Required("location"): absolute_path,
                vlp.Optional("target"): ssh_target,
                vlp.Optional("extra_options", default=()): extra_options,
            }
        )

    def check(self, role: CheckRole) -> tuple[Check, ...]:
        return ()

    def cap_source(self) -> PathTree:
        return PathTree(self.location, self.target)

    def cap_store(
        self,
        source: PathTree,
        operation: bckp.BackupOperation,
        base: RsyncTree | None = None,
    ) -> bckp.BackupArtifact[RsyncTree]:
        if base is not None and not same_endpoint(base.target, self.target):
            raise RsyncDriverError("rsync base and destination use different SSH endpoints")

        destination = RsyncTree(self.location / operation.artifact_name, self.target)
        command: list[str | ty.Path] = [
            "rsync",
            "--archive",
            "--numeric-ids",
            "--delete",
            "--protect-args",
            *self.extra_options,
        ]
        if base is not None:
            command.append(f"--link-dest={base.path}")
        command.extend((_directory(source.path), _directory(destination.path)))
        rsync_command = self._command(source.target, destination.target, command)

        try:
            self.runner.run(rsync_command)
        except BaseException:
            self._delete((destination,), check=False)
            raise
        return bckp.BackupArtifact(operation, destination)

    def cap_list(self, backup_name: str) -> tuple[bckp.BackupArtifact[RsyncTree], ...]:
        result = self.runner.run(
            command_for_target(
                self.target,
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
        artifacts = []
        for value in (result.stdout or "").splitlines():
            path = Path(value)
            try:
                operation = bckp.BackupOperation.from_artifact_name(backup_name, path.name)
            except YaesmValueError:
                continue
            artifacts.append(bckp.BackupArtifact(operation, RsyncTree(path, self.target)))
        return tuple(
            sorted(artifacts, key=lambda artifact: artifact.operation.created_at, reverse=True)
        )

    def cap_delete(
        self,
        artifacts: ty.Sequence[bckp.BackupArtifact[RsyncTree]],
    ) -> None:
        trees = tuple(artifact.representation for artifact in artifacts)
        if any(not same_endpoint(tree.target, self.target) for tree in trees):
            raise RsyncDriverError("rsync artifact uses a different SSH endpoint")
        self._delete(trees)

    def _command(
        self,
        source: SSHTarget | None,
        destination: SSHTarget | None,
        command: Command,
    ) -> tuple[str, ...]:
        if same_endpoint(source, destination):
            return command_for_target(destination, command)
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
        self.runner.run(
            command_for_target(
                trees[0].target,
                ("rm", "-rf", *(tree.path for tree in trees)),
            ),
            check=check,
        )


def _directory(path: ty.Path) -> str:
    value = str(path)
    return value if value == "/" else f"{value.rstrip('/')}/"


def _remote_directory(target: SSHTarget, path: ty.Path) -> str:
    host = f"[{target.host}]" if ":" in target.host else target.host
    destination = host if target.user is None else f"{target.user}@{host}"
    return f"{destination}:{_directory(path)}"
