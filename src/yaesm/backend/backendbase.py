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
    """

    @final
    def do_backup(self, backup:bckp.Backup, timeframe:Timeframe):
        """Perform a backup of 'backup' for the Timeframe 'timeframe'."""
        src_dir = backup.src_dir
        if isinstance(backup.dst_dir, SSHTarget):
            dst_dir = backup.dst_dir.with_path(dst_dir.path.joinpath(timeframe.name))
        else:
            dst_dir = dst_dir.joinpath(timeframe.name)
        if backup.backup_type == "local_to_local":
            self._exec_backup_local_to_local(src_dir, dst_dir)
        elif backup.backup_type == "local_to_remote":
            self._exec_backup_local_to_remote(src_dir, dst_dir)
        else: # remote_to_local
            self._exec_backup_remote_to_local(timeframe)
        backups = bckp.backups_collect(dst_dir) # sorted newest to oldest
        to_delete = []
        while len(backups) > timeframe.keep:
            to_delete.append(backups.pop())
        if to_delete:
            if (isinstance(dst_dir, SSHTarget)):
                self._delete_backups_remote(*to_delete)
            else:
                self._delete_backups_local(*to_delete)

    @abc.abstractmethod
    def _exec_backup_local_to_local(self, src_dir:Path, dst_dir:Path):
        """Execute a single local to local backup of 'src_dir' and place it in
        'dst_dir', which should represent an existing directory on the local
        system. Does not perform any cleanup.
        """
        ...

    @abc.abstractmethod
    def _exec_backup_local_to_remote(self, src_dir:Path, dst_dir:SSHTarget):
        """Execute a single local to remote backup of 'src_dir' and place it in
        the SSHTarget 'dst_dir', which should have a .path representing an
        existing directory on the remote server. Does not perform any cleanup.
        """
        ...

    @abc.abstractmethod
    def _exec_backup_remote_to_local(self, src_dir:SSHTarget, dst_dir:Path):
        """Execute a single remote to local backup of the SSHTarget 'src_dir' and
        place it in 'dst_dir', which should represent an existing directory on
        the local system. Does not perform any cleanup.
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
