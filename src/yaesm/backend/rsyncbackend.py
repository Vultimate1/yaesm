import subprocess
from pathlib import Path

import yaesm.backup as bckp
from yaesm.sshtarget import SSHTarget
from yaesm.backend.backendbase import BackendBase

class RsyncBackend(BackendBase):
    """The rysnc backup execution backend. See BackendBase for more details on
    backup execution backends in general.
    """
 

def _rsync_do_bootstrap_local(src_dir:Path, dst_dir:Path):
    bootstrap_snapshot = dst_dir.joinpath(_rsync_bootstrap_snapshot_basename())
    p = subprocess.run(["rsync", "-a", "-v", "--delete", "--numeric-ids", "--delete-excluded", f"{src_dir}/", f"{bootstrap_snapshot}/"], check=True)
    return p.returncode, bootstrap_snapshot

def _rsync_bootstrap_snapshot_local(dst_dir:Path):
    bootstrap_snapshot = dst_dir.joinpath(_rsync_bootstrap_snapshot_basename())
    if bootstrap_snapshot.is_dir():
        return bootstrap_snapshot
    else:
        return None

def _rsync_bootstrap_snapshot_basename():
    return ".yaesm-rsync-bootstrap-snapshot"
