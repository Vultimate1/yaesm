"""src/yaesm/backend/backendbase.py."""

from __future__ import annotations

import abc
import importlib
import shutil
import subprocess
from functools import cache
from pathlib import Path

import voluptuous as vlp

import yaesm.backup as bckp
import yaesm.ty as ty
from yaesm import config
from yaesm.logging import Logging
from yaesm.sshtarget import SSHTarget
from yaesm.timeframe import Timeframe


class BackendBase(abc.ABC):
    """Abstract base class for execution backend classes such as `RsyncBackend` and `BtrfsBackend`.

    Backend implementations are expected to overload
    `_exec_backup_local_to_local()`, `_exec_backup_local_to_remote()`,
    `_exec_backup_remote_to_local()`, `_delete_backups_local()`, and
    `_delete_backups_remote()`. Any code using a backend only needs to interact
    with the `do_backup()` method, which is defined in this class.

    It is important to note that it is expected that `backup.dst_dir` is an existing
    directory (Path or SSHTarget).
    """

    def __init__(self, extra_opts: list[str] | None = None) -> None:
        self.extra_opts = extra_opts

    @ty.final
    def do_backup(self, backup: bckp.Backup, timeframe: Timeframe) -> None:
        """Perform a `backup` for a given `timeframe`.

        Note that this function also cleans up old backups.
        """
        backup_basename = bckp.backup_basename_now(backup, timeframe)
        if isinstance(backup.dst_dir, SSHTarget):
            backup_exists = backup.dst_dir.exists(backup.dst_dir.path / backup_basename)
        else:
            backup_exists = backup.dst_dir.joinpath(backup_basename).exists()
        if backup_exists:
            Logging.get().error(f"backup already exists: {backup_basename}")
            raise bckp.BackupError(f"backup already exists: {backup_basename}")
        if backup.backup_type == "local_to_local":
            self._exec_backup_local_to_local(backup, backup_basename, timeframe)
        elif backup.backup_type == "local_to_remote":
            self._exec_backup_local_to_remote(backup, backup_basename, timeframe)
        else:  # remote_to_local
            self._exec_backup_remote_to_local(backup, backup_basename, timeframe)
        backups = bckp.backups_collect(backup, timeframe=timeframe)  # sorted newest to oldest
        to_delete = []
        while len(backups) > timeframe.keep:
            to_delete.append(backups.pop())
        if to_delete:
            if isinstance(backup.dst_dir, SSHTarget):
                self._delete_backups_remote(*to_delete)
            else:
                self._delete_backups_local(*to_delete)

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

    @ty.final
    def check(self, backup: bckp.Backup) -> list[str]:
        """Check that preconditions for `backup` are met.

        Returns a list of error strings. Empty list means all checks passed.
        """
        errors: list[str] = []
        src_dir = backup.src_dir
        dst_dir = backup.dst_dir
        sshtarget = src_dir if isinstance(src_dir, SSHTarget) else None
        if sshtarget is None and isinstance(dst_dir, SSHTarget):
            sshtarget = dst_dir
        if isinstance(src_dir, SSHTarget):
            errors += check_ssh_connectivity(src_dir)
            if not errors:
                errors += check_dir_exists_remote(src_dir, "src_dir")
                errors += check_dir_readable_remote(src_dir, "src_dir")
        else:
            errors += check_dir_exists_local(src_dir, "src_dir")
        if isinstance(dst_dir, SSHTarget):
            errors += check_ssh_connectivity(dst_dir)
            if not errors:
                errors += check_dir_exists_remote(dst_dir, "dst_dir")
                errors += check_dir_writable_remote(dst_dir, "dst_dir")
        else:
            errors += check_dir_exists_local(dst_dir, "dst_dir")
        errors += check_tool_local(self.name())
        if sshtarget is not None and not any("SSH" in e or "cannot" in e for e in errors):
            errors += check_tool_remote(sshtarget, self.name())
        errors += self.check_extra(backup)
        return errors

    @abc.abstractmethod
    def check_extra(self, backup: bckp.Backup) -> list[str]:
        """Backend-specific checks beyond the common ones.

        Returns a list of error strings. Empty list means all checks passed.
        """

    @abc.abstractmethod
    def _exec_backup_local_to_local(
        self, backup: bckp.Backup, backup_basename: str, timeframe: Timeframe
    ) -> None:
        """Execute a single local to local backup for the Backup `backup` in Timeframe `timeframe`.

        The resulting backup will have basename `backup_basename`.
        Note that this function does not perform any cleanup.
        """

    @abc.abstractmethod
    def _exec_backup_local_to_remote(
        self, backup: bckp.Backup, backup_basename: str, timeframe: Timeframe
    ) -> None:
        """Execute a single local to remote backup for the Backup `backup` in
        the Timeframe `timeframe`. The resulting backup backup will have
        basename `backup_basename`. Note that this function does not perform any
        cleanup.
        """

    @abc.abstractmethod
    def _exec_backup_remote_to_local(
        self, backup: bckp.Backup, backup_basename: str, timeframe: Timeframe
    ) -> None:
        """Execute a single remote to local backup for the Backup `backup` in
        the Timeframe `timeframe`. The resulting backup will have basename
        `backup_basename`. Note that this function does not perform any cleanup.
        """

    @abc.abstractmethod
    def _delete_backups_local(self, *backups: Path) -> None:
        """Delete all the local backups in `*backups` (Paths)."""

    @abc.abstractmethod
    def _delete_backups_remote(self, *backups: SSHTarget) -> None:
        """Delete all the remote backups in `*backups` (SSHTargets)."""

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
        sshtarget.openssh_cmd(f"type {tool}"),
        check=False,
        capture_output=True,
    )
    if p.returncode != 0:
        return [f"required tool not found on remote {sshtarget.host}: {tool}"]
    return []


def check_dir_readable_remote(sshtarget: SSHTarget, label: str) -> list[str]:
    p = subprocess.run(
        sshtarget.openssh_cmd(f"test -r '{sshtarget.path}'"),
        check=False,
        capture_output=True,
    )
    if p.returncode != 0:
        return [f"{label} is not readable on remote {sshtarget.host}: {sshtarget.path}"]
    return []


def check_dir_writable_remote(sshtarget: SSHTarget, label: str) -> list[str]:
    p = subprocess.run(
        sshtarget.openssh_cmd(f"test -w '{sshtarget.path}'"),
        check=False,
        capture_output=True,
    )
    if p.returncode != 0:
        return [f"{label} is not writable on remote {sshtarget.host}: {sshtarget.path}"]
    return []
