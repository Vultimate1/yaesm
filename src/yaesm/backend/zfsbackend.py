import subprocess
from pathlib import Path

from yaesm.backend.backendbase import BackendBase
import yaesm.backup as bckp
from yaesm.sshtarget import SSHTarget

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

def _zfs_snapshot_dir(dataset:str):
    mountpoint = subprocess.run(["zfs", "get", "-H", "-o", "value", "mountpoint", dataset], capture_output=True, encoding="utf-8", check=True).stdout.removesuffix("\n")
    snapshot_dir = Path(mountpoint).joinpath(".zfs", "snapshot")
    return snapshot_dir

def _zfs_take_snapshot_local(dataset:str, snapshot_name:str, recursive=False, check=True):
    """Take a zfs snapshot of 'src_dir' (which is a Path to a zfs dataset), and
    name it 'snapshot_name'. If 'recursive' is True, then add the "-r" flag to
    the "zfs snapshot" command. Passes 'check' along to subprocess.run().
    Returns a pair containing the zfs snapshot command returncode, and the name
    of the created (or attempted to create) snapshot.
    """
    snapshot = f"{dataset}@{snapshot_name}"
    if recursive:
        p = subprocess.run(["zfs", "snapshot", "-r", snapshot], check=check)
    else:
        p = subprocess.run(["zfs", "snapshot", snapshot], check=check)
    return p.returncode, snapshot

def _zfs_take_snapshot_remote(dataset:SSHTarget, snapshot_name:str, recursive=False, check=True):
    """Take a zfs snapshot of the remote 'dataset' and name it
    'snapshot_name'. If 'recursive' is True, then add the "-r" flag to the
    "zfs snapshot" command. Passes 'check' along to subprocess.run(). Returns a
    pair containing the "zfs snapshot" commands returncode, and an SSHTarget
    with its .path pointing to the name of the created (or attempted to create)
    snapshot.

    Note that the "zfs snapshot" command is executed with sudo, so the remote
    user must have passwordless sudo access to execute "zfs snapshot".
    """
    snapshot = f"{dataset.path}@{snapshot_name}"
    if recursive:
        p = subprocess.run(dataset.openssh_cmd(f"sudo -n zfs snapshot -r '{snapshot}'"), shell=True, check=check)
    else:
        p = subprocess.run(dataset.openssh_cmd(f"sudo -n zfs snapshot '{snapshot}'"), shell=True, check=check)
    return p.returncode, dataset.with_path(snapshot)
