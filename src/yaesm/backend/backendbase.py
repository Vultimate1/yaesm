"""src/yaesm/backend/backendbase.py."""

from __future__ import annotations

import abc
import dataclasses
import importlib
import logging
import shutil
import subprocess
from functools import cache
from pathlib import Path

import voluptuous as vlp

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm import config
from yaesm.sshtarget import SSHTarget
from yaesm.timeframe import Timeframe

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """The result of one backup precondition check."""

    description: str
    errors: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.errors


class BackendBase(abc.ABC):
    """Abstract base class for execution backend classes such as `RsyncBackend` and `BtrfsBackend`.

    Backend implementations are expected to implement `check()`, `create()`,
    `collect()`, and `delete()`.
    """

    def __init__(self, extra_opts: list[str] | None = None) -> None:
        self.extra_opts = extra_opts

    @ty.final
    def do_backup(self, backup: bckp.Backup, timeframe: Timeframe) -> None:
        """Perform a `backup` for a given `timeframe`.

        Note that this function also cleans up old backups.
        """
        backup_basename = bckp.backup_basename_now(backup, timeframe)
        backups = self.collect(backup, timeframes=[timeframe])
        if any(artifact.name == backup_basename for artifact in backups):
            logger.error(f"backup already exists: {backup_basename}")
            raise bckp.BackupError(f"backup already exists: {backup_basename}")
        backups.append(self.create(backup, timeframe, backup_basename))
        backups.sort(key=lambda artifact: artifact.created_at, reverse=True)
        to_delete = backups[timeframe.keep :]
        if to_delete:
            self.delete(backup, to_delete)

    @classmethod
    @ty.final
    def name(cls) -> str:
        """Automatically derive backend name from class name.

        Converts `BtrfsBackend` -> 'btrfs', `RsyncBackend` -> 'rsync', etc.
        """
        class_name = cls.__name__
        backend_name = class_name[:-7]  # Remove 'Backend' suffix
        return backend_name.lower()

    @staticmethod
    def config_settings() -> set[str]:
        """Returns the set of valid configuration setting names for this backend."""
        return set()

    @staticmethod
    def config_schema() -> vlp.Schema:
        """Returns a voluptuous schema for this backends specific configuration.

        See the yaesm.config module for more information.
        """
        return config.Schema.schema_empty()

    @staticmethod
    def config_schema_extra() -> vlp.Schema:
        """Returns a voluptuous schema to be applied to the configuration data circumstantially.

        More complicated or IO-driven validation should happen in this schema.
        See the yaesm.config module for more information.
        """
        return config.Schema.schema_empty()

    @abc.abstractmethod
    def check(self, backup: bckp.Backup) -> list[CheckResult]:
        """Check that preconditions for `backup` are met."""

    @abc.abstractmethod
    def create(self, backup: bckp.Backup, timeframe: Timeframe, name: str) -> bckp.BackupArtifact:
        """Create and return a stored backup artifact."""

    @abc.abstractmethod
    def collect(
        self, backup: bckp.Backup, timeframes: list[Timeframe] | None = None
    ) -> list[bckp.BackupArtifact]:
        """Collect stored backup artifacts from newest to oldest."""

    @abc.abstractmethod
    def delete(self, backup: bckp.Backup, artifacts: list[bckp.BackupArtifact]) -> None:
        """Delete stored backup artifacts."""

    @staticmethod
    @ty.final
    @cache
    def backend_classes() -> list[type[BackendBase]]:
        """Returns a list of all the backend classes.

        This is made possible with the use of a naming convention for backend
        classes. Backend modules named "yaesm.backend.${BACKEND_NAME_LOWERCASE}backend".
        Within each backend module there is a class named "${BACKEND_NAME_CAPITALIZED}Backend".
        """
        backend_dir = Path(__file__).parent
        backend_files = backend_dir.glob("*backend.py")
        backend_classes = []
        for f in backend_files:
            class_name = f.stem.replace("backend", "").capitalize() + "Backend"
            module_name = f"yaesm.backend.{class_name.lower()}"
            module = importlib.import_module(module_name)
            backend_class = getattr(module, class_name)
            backend_classes.append(backend_class)
        return backend_classes


class PathBackendBase(BackendBase):
    """Base class for backends with Path or SSHTarget sources and destinations.

    The destination is expected to be an existing directory.
    """

    @ty.final
    def check(self, backup: bckp.Backup) -> list[CheckResult]:
        """Check that path backup preconditions are met."""
        results: list[CheckResult] = []

        def add_result(description: str, errors: list[str]) -> CheckResult:
            result = CheckResult(description, tuple(errors))
            results.append(result)
            return result

        src_dir = backup.src_dir
        dst_dir = backup.dst_dir
        sshtarget = src_dir if isinstance(src_dir, SSHTarget) else None
        if isinstance(dst_dir, SSHTarget):
            sshtarget = dst_dir
        ssh_connected = True
        if sshtarget is not None:
            ssh_connected = add_result(
                f"SSH connection to {sshtarget.host}", check_ssh_connectivity(sshtarget)
            ).passed

        if isinstance(src_dir, SSHTarget):
            if ssh_connected:
                add_result(
                    f"src_dir exists on remote {src_dir.host}: {src_dir.path}",
                    check_dir_exists_remote(src_dir, "src_dir"),
                )
                add_result(
                    f"src_dir is readable on remote {src_dir.host}: {src_dir.path}",
                    check_dir_readable_remote(src_dir, "src_dir"),
                )
        else:
            add_result(
                f"src_dir exists locally: {src_dir}",
                check_dir_exists_local(src_dir, "src_dir"),
            )

        if isinstance(dst_dir, SSHTarget):
            if ssh_connected:
                add_result(
                    f"dst_dir exists on remote {dst_dir.host}: {dst_dir.path}",
                    check_dir_exists_remote(dst_dir, "dst_dir"),
                )
                add_result(
                    f"dst_dir is writable on remote {dst_dir.host}: {dst_dir.path}",
                    check_dir_writable_remote(dst_dir, "dst_dir"),
                )
        else:
            add_result(
                f"dst_dir exists locally: {dst_dir}",
                check_dir_exists_local(dst_dir, "dst_dir"),
            )

        add_result(
            f"{self.name()} is installed locally",
            check_tool_local(self.name()),
        )
        if sshtarget is not None and ssh_connected:
            add_result(
                f"{self.name()} is installed on remote {sshtarget.host}",
                check_tool_remote(sshtarget, self.name()),
            )
        results += self.check_extra(backup)
        return results

    def collect(
        self, backup: bckp.Backup, timeframes: list[Timeframe] | None = None
    ) -> list[bckp.BackupArtifact]:
        """Collect directory-backed artifacts from newest to oldest."""
        return bckp.path_artifacts_collect(backup, timeframes=timeframes)

    def check_extra(self, backup: bckp.Backup) -> list[CheckResult]:
        """Perform backend-specific path checks."""
        return []


def check_dir_exists_local(path: Path, label: str) -> list[str]:
    if not path.is_dir():
        return [f"{label} does not exist locally: {path}"]
    return []


def check_dir_exists_remote(sshtarget: SSHTarget, label: str) -> list[str]:
    if not sshtarget.is_dir():
        return [f"{label} does not exist on remote {sshtarget.host}: {sshtarget.path}"]
    return []


def check_ssh_connectivity(sshtarget: SSHTarget) -> list[str]:
    if not sshtarget.can_connect():
        return [f"cannot establish SSH connection to {sshtarget.host}"]
    return []


def check_tool_local(tool: str) -> list[str]:
    if shutil.which(tool) is None:
        return [f"required tool not found locally: {tool}"]
    return []


def check_tool_remote(sshtarget: SSHTarget, tool: str) -> list[str]:
    p = subprocess.run(
        sshtarget.openssh_cmd(["command", "-v", tool]),
        check=False,
        capture_output=True,
    )
    if p.returncode != 0:
        return [f"required tool not found on remote {sshtarget.host}: {tool}"]
    return []


def check_dir_readable_remote(sshtarget: SSHTarget, label: str) -> list[str]:
    p = subprocess.run(
        sshtarget.openssh_cmd(["test", "-r", sshtarget.path]),
        check=False,
        capture_output=True,
    )
    if p.returncode != 0:
        return [f"{label} is not readable on remote {sshtarget.host}: {sshtarget.path}"]
    return []


def check_dir_writable_remote(sshtarget: SSHTarget, label: str) -> list[str]:
    p = subprocess.run(
        sshtarget.openssh_cmd(["test", "-w", sshtarget.path]),
        check=False,
        capture_output=True,
    )
    if p.returncode != 0:
        return [f"{label} is not writable on remote {sshtarget.host}: {sshtarget.path}"]
    return []
