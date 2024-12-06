import subprocess
from pathlib import Path

import yaesm.backup as bckp
from yaesm.sshtarget import SSHTarget
from yaesm.backend.backendbase import BackendBase

class BtrfsBackend(BackendBase):
    """The btrfs backup execution backend. See BackendBase for more details."""

    def _exec_backup_local_to_local(self, src_dir:Path, dst_dir:Path):
        returncode, _ = _take_snapshot_local(src_dir, dst_dir, add_backup_basename_now=True, check=False)
        if 0 != returncode:
            bootstrap_snapshot = _bootstrap_local_to_local(src_dir, dst_dir)
            _, tmp_snapshot = _take_btrfs_snapshot_local(src_dir, src_dir, add_backup_basename_now=True)
            _btrfs_send_receive_local_to_local(tmp_snapshot, dst_dir, parent=bootstrap_snapshot)
            _delete_subvolumes_local(tmp_snapshot)

    def _exec_backup_local_to_remote(self, src_dir:Path, dst_dir:SSHTarget):
        bootstrap_snapshot = btrfs_bootstrap_init_local_to_remote(src_dir, dst_dir)
        _, tmp_snapshot = _take_btrfs_snapshot_local(src_dir, src_dir, add_backup_basename_now=True)
        _btrfs_send_receive_local_to_remote(tmp_snapshot, dst_dir, parent=bootstrap_snapshot)
        _delete_btrfs_subvolumes_local(tmp_snapshot)

    def _exec_backup_remote_to_local(self, src_dir:SSHTarget, dst_dir:Path):
        bootstrap_snapshot = _btrfs_bootstrap_init_remote_to_local(src_dir, dst_dir)
        _, tmp_snapshot = _take_btrfs_snapshot_remote(src_dir, src_dir, add_backup_basename_now=True)
        _btrfs_send_receive_remote_to_local(tmp_snapshot, dst_dir, parent=bootstrap_snapshot)
        _delete_btrfs_subvolumes_remote(tmp_snapshot)

    def _delete_backups_local(self, *backups):
        _delete_btrfs_subvolumes_local(*backups)

    def _delete_backups_remote(self, *backups):
        _delete_btrfs_subvolumes_remote(*backups)

def _btrfs_take_snapshot_local(src_dir:Path, dst_dir:Path, add_backup_basename_now=False, check=True):
    """Take a readonly local btrfs snapshot of 'src_dir', and place it in
    'dst_dir'. If 'add_backup_basename_now' is True, then use
    bckp.backup_basename_now() to generate the basename of the snapshot
    (otherwise the snapshot name is the same as the 'dst_dir'). Passes 'check'
    along to subprocess.run(). Returns a pair containing the btrfs subvolume
    snapshot command returncode, and the name of the created (or attempted to create)
    snapshot.
    """
    if add_backup_basename_now:
        snapshot = dst_dir.joinpath(bckp.backup_basename_now())
    else:
        snapshot = dst_dir
    p = subprocess.run(["btrfs", "subvolume", "snapshot", "-r", src_dir, snapshot], check=check)
    return p.returncode, snapshot

def _btrfs_take_snapshot_remote(src_dir:SSHTarget, dst_dir:SSHTarget, add_backup_basename_now=False, check=True):
    """Take a readonly btrfs snapshot of the remote 'src_dir', and place it in
    the remote 'dst_dir'. If 'add_backup_basename_now' is True, then use
    bckp.backup_basename_now() to generate the basename of the snapshot
    (otherwise the snapshot name is the same as the 'dst_dir.path'). Passes 'check'
    along to subprocess.run(). Returns a pair containing the btrfs subvolume
    snapshot command returncode, and the name of the created (or attempted to create)
    snapshot.

    Note that it is assumed that 'src_dir' and 'dst_dir' refer to the same SSH
    server. Also note that the btrfs command is executed with sudo, so the remote
    user must have passwordless sudo access to execute 'btrfs subvolume snapshot'.
    """
    if add_backup_basename_now:
        snapshot = dst_dir.with_path(dst_dir.path.joinpath(bckp.backup_basename_now()))
    else:
        snapshot = dst_dir.with_path(dst_dir.path)
    p = subprocess.run(src_dir.openssh_cmd(f"sudo -n btrfs subvolume snapshot -r '{src_dir.path}' '{snapshot.path}'"), shell=True, check=check)
    return p.returncode, snapshot

def _btrfs_delete_subvolumes_local(*subvolumes, check=True):
    """Delete all the local btrfs subvolumes in '*subvolumes' (a list of Paths).
    The 'check' arg is passed along to subprocess.run(). Returns a pair
    containing the 'btrfs subvolume delete' commands returncode, and a list of all
    the deleted subvolumes (a list of Paths).
    """
    p = subprocess.run(["btrfs", "subvolume", "delete", *subvolumes], check=check)
    return p.returncode, list(subvolumes)

def _btrfs_delete_subvolumes_remote(*subvolumes, check=True):
    """Delete all the remote btrfs subvolumes in '*subvolumes' (a list of SSHTargets).
    The 'check' arg is passed along to subprocess.run(). Returns a pair
    containing the 'btrfs subvolume delete' commands returncode, and a list of all
    the deleted subvolumes (a list of SSHTargets).

    Note that it is assumed that all the SSHTargets in '*subvolumes' refer to
    the same SSH server. Also note that the 'btrfs subvolume delete' command is
    run with sudo, so the remote user must have passwordless sudo access to
    execute 'btrfs subvolume delete'.
    """
    _subvolumes = ""
    for subvolume in subvolumes:
        _subvolumes = f"{_subvolumes} '{subvolume.path}'"
    p = subprocess.run(subvolumes[0].openssh_cmd(f"sudo -n btrfs subvolume delete {_subvolumes}"), shell=True, check=check)
    return p.returncode, list(subvolumes)

def _btrfs_send_receive_local_to_local(snapshot:Path, dst_dir:Path, parent=None, check=True):
    """Perform a btrfs send/receive of a local snapshot to the local dir
    'dst_dir'. If supplied a 'parent' arg, then uses btrfs send '-p' for an
    incremental backup. Passes along 'check' to subprocess.run().
    """
    parent_opt = "" if parent is None else f"-p '{parent}'"
    p = subprocess.run(f"btrfs send {parent_opt} '{snapshot}' | btrfs receive '{dst_dir}'", shell=True, check=check)
    return p.returncode, dst_dir.joinpath(snapshot.name)

def _btrfs_send_receive_local_to_remote(snapshot:Path, dst_dir:SSHTarget, parent=None, check=True):
    """Perform a btrfs send/receive of a local snapshot to the SSHTarget
    'dst_dir'. If supplied a 'parent' arg, then uses btrfs send '-p' for an
    incremental backup. Passes along 'check' to subprocess.run().
    """
    parent_opt = "" if parent is None else f"-p '{parent}'"
    p = subprocess.run(f"btrfs send {parent_opt} '{snapshot}'" + " | " + dst_dir.openssh_cmd(f"sudo -n btrfs receive '{dst_dir.path}'"), shell=True, check=check)
    return p.returncode, dst_dir.with_path(dst_dir.path.joinpath(snapshot.name))

def _btrfs_send_receive_remote_to_local(snapshot:SSHTarget, dst_dir:Path, parent=None, check=True):
    """Perform a btrfs send/receive of a remote snapshot to the local dir
    'dst_dir'. If supplied a 'parent' arg, then uses btrfs send '-p' for an
    incremental backup. Passes along 'check' to subprocess.run().
    """
    parent_opt = "" if parent is None else f"-p '{parent.path}'"
    p = subprocess.run(snapshot.openssh_cmd(f"sudo -n btrfs send {parent_opt} '{snapshot.path}'") + " | " + f"btrfs receive '{dst_dir}'", shell=True, check=check)
    return p.returncode, dst_dir.joinpath(snapshot.path.name)

def _btrfs_bootstrap_snapshot_basename():
    """Return the basename of a btrfs bootstrap snapshot."""
    return ".yaesm-btrfs-bootstrap-snapshot"

def _btrfs_bootstrap_local_to_local(src_dir:Path, dst_dir:Path):
    """TODO"""
    src_bootstrap = src_dir.joinpath(_btrfs_bootstrap_snapshot_basename())
    dst_bootstrap = dst_dir.joinpath(_btrfs_bootstrap_snapshot_basename())
    src_bootstrap_exists = src_bootstrap.is_dir()
    dst_bootstrap_exists = dst_bootstrap.is_dir()
    if not src_bootstrap_exists and not dst_bootstrap_exists:
        _btrfs_take_snapshot_local(src_dir, src_bootstrap)
        _btrfs_send_receive_local_to_local(src_bootstrap, dst_dir)
    elif src_bootstrap_exists and not dst_bootstrap_exists:
        _btrfs_send_receive_local_to_local(src_bootstrap, dst_dir)
    elif not src_bootstrap_exists and dst_bootstrap_exists:
        # TODO: should log here, something weird is going on
        _btrfs_delete_subvolumes_local(dst_bootstrap)
        _btrfs_take_snapshot_local(src_dir, src_bootstrap)
        _btrfs_send_receive_local_to_local(src_bootstrap, dst_dir)
    else:
        pass # already bootstrapped
    return src_bootstrap

def _btrfs_bootstrap_local_to_remote(src_dir:Path, dst_dir:SSHTarget):
    """TODO"""
    src_bootstrap = src_dir.joinpath(_btrfs_bootstrap_snapshot_basename())
    dst_bootstrap = dst_dir.with_path(dst_dir.path.joinpath(_btrfs_bootstrap_snapshot_basename()))
    src_bootstrap_exists = src_bootstrap.is_dir()
    dst_bootstrap_exists = 0 == subprocess.run(dst_bootstrap.openssh_cmd(f"[ -d '{dst_bootstrap.path}' ]; exit $?"), shell=True).returncode
    if not src_bootstrap_exists and not dst_bootstrap_exists:
        _btrfs_take_snapshot_local(src_dir, src_bootstrap)
        _btrfs_send_receive_local_to_remote(src_bootstrap, dst_dir)
    elif src_bootstrap_exists and not dst_bootstrap_exists:
        _btrfs_send_receive_local_to_remote(src_bootstrap, dst_dir)
    elif not src_bootstrap_exists and dst_bootstrap_exists:
        # TODO: should log here, something weird is going on
        _btrfs_delete_subvolumes_remote(dst_bootstrap)
        _btrfs_take_snapshot_local(src_dir, src_bootstrap)
        _btrfs_send_receive_local_to_remote(src_bootstrap, dst_dir)
    else:
        pass # already bootstrapped
    return src_bootstrap

def _btrfs_bootstrap_remote_to_local(src_dir:SSHTarget, dst_dir:Path):
    """TODO"""
    src_bootstrap = src_dir.with_path(src_dir.path.joinpath(_btrfs_bootstrap_snapshot_basename()))
    dst_bootstrap = dst_dir.joinpath(_btrfs_bootstrap_snapshot_basename())
    src_bootstrap_exists = 0 == subprocess.run(src_bootstrap.openssh_cmd(f"[ -d '{src_bootstrap.path}' ]; exit $?"), shell=True).returncode
    dst_bootstrap_exists = dst_bootstrap.is_dir()
    if not src_bootstrap_exists and not dst_bootstrap_exists:
        _btrfs_take_snapshot_remote(src_dir, src_bootstrap)
        _btrfs_send_receive_remote_to_local(src_bootstrap, dst_dir)
    elif src_bootstrap_exists and not dst_bootstrap_exists:
        _btrfs_send_receive_remote_to_local(src_bootstrap, dst_dir)
    elif not src_bootstrap_exists and dst_bootstrap_exists:
        # TODO: should log here, something weird is going on
        _btrfs_delete_subvolumes_local(dst_bootstrap)
        _btrfs_take_snapshot_remote(src_dir, src_bootstrap)
        _btrfs_send_receive_remote_to_local(src_bootstrap, dst_dir)
    else:
        pass # already bootstrapped
    return src_bootstrap
