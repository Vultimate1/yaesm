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
            self._exec_backup_local_to_local(backup.src_dir, backup.dst_dir.joinpath(backup_basename))
        elif backup.backup_type == "local_to_remote":
            backup_path = backup.dst_dir.with_path(backup.dst_dir.path.joinpath(backup_basename))
            self._exec_backup_local_to_remote(backup.src_dir, backup_path)
        else: # remote_to_local
            self._exec_backup_remote_to_local(backup.src_dir, backup.dst_dir.joinpath(backup_basename))
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
    def _exec_backup_local_to_local(self, src_dir:Path, backup_path:Path):
        """Execute a single local to local backup of 'src_dir' and place it at
        'backup_path', whos parent dir should be an existing directory on the
        local system, and whos basename will be the backup name. Does not
        perform any cleanup.
        """
        ...

    @abc.abstractmethod
    def _exec_backup_local_to_remote(self, src_dir:Path, backup_path:SSHTarget):
        """Execute a single local to remote backup of 'src_dir' and place it at
        the SSHTarget 'backup_path', which should have a .path whos parent should
        be an existing directory on the remote server, and whos basename will
        be the backup name. Does not perform any cleanup.
        """
        ...

    @abc.abstractmethod
    def _exec_backup_remote_to_local(self, src_dir:SSHTarget, backup_path:Path):
        """Execute a single remote to local backup of the SSHTarget 'src_dir'
        and place it at the local 'backup_path', whos parent dir should be an
        existing directory on the local system, and whos basename will be the
        backup name. Does not perform any cleanup.
        """
        ...

    @abc.abstractmethod
    def _delete_backups_local(self, *backups):
        """Delete all the local backups in '*backups' (Paths)."""
        ...

    @abc.abstractmethod
    def _delete_backups_remote(self, *backups):
        """Delete all the remote backups in '*backups' (SSHTargets)."""
        ...
