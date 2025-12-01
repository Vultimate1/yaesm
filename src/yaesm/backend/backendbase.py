import abc
from typing import final
from pathlib import Path

import yaesm.backup as bckp
from yaesm.timeframe import Timeframe
from yaesm.sshtarget import SSHTarget

class BackendBase(abc.ABC):
    """Abstract base class for execution backend classes such as RsyncBackend
    and BtrfsBackend. An actual backend class inherits from BackendBase, and
    implements the methods '_exec_backup_local_to_local()',
    '_exec_backup_local_to_remote()', '_exec_backup_remote_to_local()',
    '_delete_backups_local()', and '_delete_backups_remote()' . Any code using a
    backend only needs to interact with the 'do_backup()' method, which is
    defined in this class.

    It is important to note that it is expected that backup.dst_dir is an existing
    directory (regardless of if it is a Path or SSHTarget).
    """
    @final
    def do_backup(self, backup:bckp.Backup, timeframe:Timeframe):
        """Perform a backup of 'backup' for the Timeframe 'timeframe'. Note that
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

    @abc.abstractmethod
    def _exec_backup_local_to_local(self, backup:bckp.Backup, backup_basename:str, timeframe:Timeframe):
        """Execute a single local to local backup for the Backup `backup` in the
        Timeframe `timeframe`. The resulting backup will have basename
        `backup_basename`. Note that this function does not perform any cleanup.
        """
        ...

    @abc.abstractmethod
    def _exec_backup_local_to_remote(self, backup:bckp.Backup, backup_basename:str, timeframe:Timeframe):
        """Execute a single local to remote backup for the Backup `backup` in
        the Timeframe `timeframe`. The resulting backup backup will have
        basename `backup_basename`. Note that this function does not perform any
        cleanup.
        """
        ...

    @abc.abstractmethod
    def _exec_backup_remote_to_local(self, backup:bckp.Backup, backup_basename:str, timeframe:Timeframe):
        """Execute a single remote to local backup for the Backup `backup` in
        the Timeframe `timeframe`. The resulting backup will have basename
        `backup_basename`. Note that this function does not perform any cleanup.
        """
        ...

    @abc.abstractmethod
    def _delete_backups_local(self, *backups):
        """Delete all the local backups in `*backups` (Paths)."""
        ...

    @abc.abstractmethod
    def _delete_backups_remote(self, *backups):
        """Delete all the remote backups in `*backups` (SSHTargets)."""
        ...
