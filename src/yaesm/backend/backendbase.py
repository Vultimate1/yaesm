"""src/yaesm/backend/backendbase.py"""

import abc
from typing import final
from pathlib import Path
import importlib
from functools import cache

import voluptuous as vlp

import yaesm.backup as bckp
from yaesm import config
from yaesm.timeframe import Timeframe
from yaesm.sshtarget import SSHTarget

class BackendBase(abc.ABC):
    """Abstract base class for execution backend classes such as `RsyncBackend`
    and `BtrfsBackend`. Backend implementations are expected to overload
    `_exec_backup_local_to_local()`, `_exec_backup_local_to_remote()`,
    `_exec_backup_remote_to_local()`, `_delete_backups_local()`, and
    `_delete_backups_remote()`. Any code using a backend only needs to interact
    with the `do_backup()` method, which is defined in this class.

    It is important to note that it is expected that `backup.dst_dir` is an existing
    directory (Path or SSHTarget).
    """
    def __init__(self, extra_opts=None):
        self.extra_opts = extra_opts

    @final
    def do_backup(self, backup:bckp.Backup, timeframe:Timeframe):
        """Perform a `backup` for a given `timeframe`. Note that
        this function also cleans up old backups.
        """
        backup_basename = bckp.backup_basename_now(backup, timeframe)
        if backup.backup_type == "local_to_local":
            self._exec_backup_local_to_local(backup, backup_basename, timeframe)
        elif backup.backup_type == "local_to_remote":
            self._exec_backup_local_to_remote(backup, backup_basename, timeframe)
        else: # remote_to_local
            self._exec_backup_remote_to_local(backup, backup_basename, timeframe)
        backups = bckp.backups_collect(backup, timeframe=timeframe) # sorted newest to oldest
        to_delete = []
        while len(backups) > timeframe.keep:
            to_delete.append(backups.pop())
        if to_delete:
            if isinstance(backup.dst_dir, SSHTarget):
                self._delete_backups_remote(*to_delete)
            else:
                self._delete_backups_local(*to_delete)

    @final
    @classmethod
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
        """Returns a voluptuous schema to be applied to the configuration data
        circumstantially. More complicated or IO-driven validation should happen
        in this schema. See the yaesm.config module for more information.
        """
        return config.Schema.schema_empty()

    @abc.abstractmethod
    def _exec_backup_local_to_local(self, backup:bckp.Backup, backup_basename:str,
                                    timeframe:Timeframe):
        """Execute a single local to local backup for the Backup `backup` in the
        Timeframe `timeframe`. The resulting backup will have basename
        `backup_basename`. Note that this function does not perform any cleanup.
        """

    @abc.abstractmethod
    def _exec_backup_local_to_remote(self, backup:bckp.Backup, backup_basename:str,
                                     timeframe:Timeframe):
        """Execute a single local to remote backup for the Backup `backup` in
        the Timeframe `timeframe`. The resulting backup backup will have
        basename `backup_basename`. Note that this function does not perform any
        cleanup.
        """

    @abc.abstractmethod
    def _exec_backup_remote_to_local(self, backup:bckp.Backup, backup_basename:str,
                                     timeframe:Timeframe):
        """Execute a single remote to local backup for the Backup `backup` in
        the Timeframe `timeframe`. The resulting backup will have basename
        `backup_basename`. Note that this function does not perform any cleanup.
        """

    @abc.abstractmethod
    def _delete_backups_local(self, *backups):
        """Delete all the local backups in `*backups` (Paths)."""

    @abc.abstractmethod
    def _delete_backups_remote(self, *backups):
        """Delete all the remote backups in `*backups` (SSHTargets)."""

    @final
    @cache
    @staticmethod
    def backend_classes():
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
