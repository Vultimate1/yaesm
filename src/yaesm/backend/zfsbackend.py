from pathlib import Path

from yaesm.backend.backendbase import BackendBase

class ZfsBackend(BackendBase):
    """The ZFS backup execution backend. See BackendBase for more details on
    backup execution backends in general.
    """
    def _exec_backup_local_to_local(self, src_dir:Path, backup_path:Path):
        ...

    def _exec_backup_local_to_remote(self, src_dir:Path, backup_path:SSHTarget):
        ...

    def _exec_backup_remote_to_local(self, src_dir:SSHTarget, backup_path:Path):
        ...

    def _delete_backups_local(self, *backups):
        ...

    def _delete_backups_remote(self, *backups):
        ...
