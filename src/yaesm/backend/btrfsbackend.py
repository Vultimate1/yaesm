"""src/yaesm/backend/btrfsbackend.py."""

import shlex
import subprocess
from pathlib import Path

import yaesm.backup as bckp
from yaesm.backend.backendbase import BackendBase
from yaesm.sshtarget import SSHTarget
from yaesm.timeframe import Timeframe


class BtrfsBackend(BackendBase):
    """The btrfs backup execution backend. See `BackendBase` for more details on
    backup execution backends in general.

    Btrfs backups are performed with a simple 'btrfs subvolume snapshot -r'
    command when possible, otherwise with incremental backups via
    'btrfs send -p ... | btrfs receive ...'. This makes btrfs backups fast and
    efficient.

    Note that if local-to-remote or remote-to-local btrfs backups are to be
    peformed, then the remote SSH user must have sufficient privileges to run
    'btrfs subvolume snapshot', 'btrfs subvolume delete', 'btrfs send', and
    'btrfs receive'.
    """

    def check_extra(self, backup: bckp.Backup) -> list[str]:
        errors: list[str] = []
        src_dir = backup.src_dir
        dst_dir = backup.dst_dir
        if isinstance(src_dir, SSHTarget):
            errors += check_btrfs_filesystem_remote(src_dir, "src_dir")
        else:
            errors += check_btrfs_filesystem_local(src_dir, "src_dir")
        if isinstance(dst_dir, SSHTarget):
            errors += check_btrfs_filesystem_remote(dst_dir, "dst_dir")
        else:
            errors += check_btrfs_filesystem_local(dst_dir, "dst_dir")
        return errors

    def _exec_backup_local_to_local(
        self, backup: bckp.Backup, backup_basename: str, timeframe: Timeframe
    ) -> None:
        assert isinstance(backup.src_dir, Path)
        assert isinstance(backup.dst_dir, Path)
        src_dir = backup.src_dir
        backup_path = backup.dst_dir.joinpath(bckp.backup_basename_now(backup, timeframe))
        returncode, _ = _btrfs_take_snapshot_local(src_dir, backup_path, check=False)
        if returncode != 0:
            bootstrap_snapshot = _btrfs_bootstrap_local_to_local(src_dir, backup_path.parent)
            _, tmp_snapshot = _btrfs_take_snapshot_local(src_dir, src_dir.joinpath(backup_basename))
            _btrfs_send_receive_local_to_local(
                tmp_snapshot, backup_path.parent, parent=bootstrap_snapshot
            )
            _btrfs_delete_subvolumes_local(tmp_snapshot)

    def _exec_backup_local_to_remote(
        self, backup: bckp.Backup, backup_basename: str, timeframe: Timeframe
    ) -> None:
        assert isinstance(backup.src_dir, Path)
        assert isinstance(backup.dst_dir, SSHTarget)
        src_dir = backup.src_dir
        backup_path = backup.dst_dir.with_path(backup.dst_dir.path.joinpath(backup_basename))
        bootstrap_snapshot = _btrfs_bootstrap_local_to_remote(
            src_dir, backup_path.with_path(backup_path.path.parent)
        )
        _, tmp_snapshot = _btrfs_take_snapshot_local(
            src_dir, src_dir.joinpath(backup_path.path.name)
        )
        _btrfs_send_receive_local_to_remote(
            tmp_snapshot, backup_path.with_path(backup_path.path.parent), parent=bootstrap_snapshot
        )
        _btrfs_delete_subvolumes_local(tmp_snapshot)

    def _exec_backup_remote_to_local(
        self, backup: bckp.Backup, backup_basename: str, timeframe: Timeframe
    ) -> None:
        assert isinstance(backup.src_dir, SSHTarget)
        assert isinstance(backup.dst_dir, Path)
        src_dir = backup.src_dir
        backup_path = backup.dst_dir.joinpath(backup_basename)
        bootstrap_snapshot = _btrfs_bootstrap_remote_to_local(src_dir, backup_path.parent)
        _, tmp_snapshot = _btrfs_take_snapshot_remote(
            src_dir, src_dir.with_path(src_dir.path.joinpath(backup_path.name))
        )
        _btrfs_send_receive_remote_to_local(
            tmp_snapshot, backup_path.parent, parent=bootstrap_snapshot
        )
        _btrfs_delete_subvolumes_remote(tmp_snapshot)

    def _delete_backups_local(self, *backups: Path) -> None:
        _btrfs_delete_subvolumes_local(*backups)

    def _delete_backups_remote(self, *backups: SSHTarget) -> None:
        _btrfs_delete_subvolumes_remote(*backups)


def _btrfs_take_snapshot_local(
    src_dir: Path, snapshot: Path, check: bool = True
) -> tuple[int, Path]:
    """Take a readonly local btrfs snapshot of `src_dir`, and place it at
    `snapshot`. The name of the created snapshot will be exactly `snapshot`.
    Passes `check` along to `subprocess.run()`. Returns a pair containing the
    btrfs subvolume snapshot command returncode, and the name of the created (or
    attempted to create) snapshot.
    """
    p = subprocess.run(["btrfs", "subvolume", "snapshot", "-r", src_dir, snapshot], check=check)
    return p.returncode, snapshot


def _btrfs_take_snapshot_remote(
    src_dir: SSHTarget, snapshot: SSHTarget, check: bool = True
) -> tuple[int, SSHTarget]:
    """Take a readonly btrfs snapshot of the remote `src_dir` and place it at
    `snapshot`. The name of the created snapshot will be exactly
    `snapshot.path`. Passes `check` along to `subprocess.run()`. Returns a pair
    containing the btrfs subvolume snapshot command returncode, and an SSHTarget
    with its .path pointing to the created (or attempted to create) snapshot.

    Note that it is assumed that `src_dir` and `snapshot` refer to the same SSH
    server.
    """
    p = subprocess.run(
        src_dir.openssh_cmd(
            f"btrfs subvolume snapshot -r '{src_dir.path}' '{snapshot.path}'"
        ),
        check=check,
    )
    return p.returncode, snapshot


def _btrfs_delete_subvolumes_local(*subvolumes: Path, check: bool = True) -> tuple[int, list[Path]]:
    """Delete all the local btrfs subvolumes in `subvolumes` (a list of Paths).
    The `check arg is passed along to `subprocess.run()`. Returns a pair
    containing the 'btrfs subvolume delete' commands returncode, and a list of all
    the deleted subvolumes (a list of Paths).
    """
    p = subprocess.run(["btrfs", "subvolume", "delete", *subvolumes], check=check)
    return p.returncode, list(subvolumes)


def _btrfs_delete_subvolumes_remote(
    *subvolumes: SSHTarget, check: bool = True
) -> tuple[int, list[SSHTarget]]:
    """Delete all the remote btrfs subvolumes in `subvolumes` (a list of SSHTargets).
    The `check` arg is passed along to `subprocess.run()`. Returns a pair
    containing the 'btrfs subvolume delete' commands returncode, and a list of all
    the deleted subvolumes (a list of SSHTargets).

    Note that it is assumed that all the SSHTargets in `subvolumes` refer to
    the same SSH server.
    """
    _subvolumes = ""
    for subvolume in subvolumes:
        _subvolumes = f"{_subvolumes} '{subvolume.path}'"
    p = subprocess.run(
        subvolumes[0].openssh_cmd(f"btrfs subvolume delete {_subvolumes}"),
        check=check,
    )
    return p.returncode, list(subvolumes)


def _btrfs_send_receive_local_to_local(
    snapshot: Path, dst_dir: Path, parent: Path | None = None, check: bool = True
) -> tuple[int, Path]:
    """Perform a btrfs send/receive sending the local snapshot `snapshot` to the
    directory `dst_dir`. If supplied a `parent` arg, then uses btrfs to send
    '-p parent' for an incremental backup. Passes along `check` to `subprocess.run()`.
    """
    parent_opt = "" if parent is None else f"-p {shlex.quote(str(parent))}"
    p = subprocess.run(
        f"btrfs send {parent_opt} {shlex.quote(str(snapshot))}"
        f" | btrfs receive {shlex.quote(str(dst_dir))}",
        shell=True,
        check=check,
    )
    return p.returncode, dst_dir.joinpath(snapshot.name)


def _btrfs_send_receive_local_to_remote(
    snapshot: Path, dst_dir: SSHTarget, parent: Path | None = None, check: bool = True
) -> tuple[int, SSHTarget]:
    """Perform a btrfs send/receive sending the local snapshot `snapshot` to the
    remote SSHTarger `dst_dir`. If supplied a 'parent' arg, then uses btrfs send
    '-p parent' for an incremental backup. Passes along `check` to `subprocess.run()`.
    """
    parent_opt = "" if parent is None else f"-p {shlex.quote(str(parent))}"
    cmd_local = f"btrfs send {parent_opt} {shlex.quote(str(snapshot))}"
    cmd_remote = dst_dir.openssh_cmd(f"btrfs receive '{dst_dir.path}'", string=True)
    p = subprocess.run(
        cmd_local + " | " + cmd_remote,
        shell=True,
        check=check,
    )
    return p.returncode, dst_dir.with_path(dst_dir.path.joinpath(snapshot.name))


def _btrfs_send_receive_remote_to_local(
    snapshot: SSHTarget, dst_dir: Path, parent: SSHTarget | None = None, check: bool = True
) -> tuple[int, Path]:
    """Perform a btrfs send/receive sending the remote snapshot `snapshot` to the
    local dir `dst_dir`. If supplied a `parent` arg, then uses btrfs send
    '-p parent' for an incremental backup. Passes along `check` to `subprocess.run()`.

    Note that if `parent` is supplied, then it is assumed to be an SSHTarget
    refering to the same SSH server as `snapshot`.
    """
    parent_opt = "" if parent is None else f"-p {shlex.quote(str(parent.path))}"
    cmd_remote = snapshot.openssh_cmd(
        f"btrfs send {parent_opt} '{snapshot.path}'", string=True
    )
    p = subprocess.run(
        cmd_remote + " | " + f"btrfs receive {shlex.quote(str(dst_dir))}",
        shell=True,
        check=check,
    )
    return p.returncode, dst_dir.joinpath(snapshot.path.name)


def _btrfs_bootstrap_snapshot_basename() -> str:
    """Return the basename of a btrfs bootstrap snapshot."""
    return ".yaesm-btrfs-bootstrap-snapshot"


def _btrfs_bootstrap_local_to_local(src_dir: Path, dst_dir: Path) -> Path:
    """Perform the bootstrap phase of a local-to-local backup.

    The bootstrap snapshot is necessary for incremental backups with 'btrfs send -p'.
    """
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
        pass  # already bootstrapped
    return src_bootstrap


def _btrfs_bootstrap_local_to_remote(src_dir: Path, dst_dir: SSHTarget) -> Path:
    """Perform the bootstrap phase of a local-to-remote backup.

    The bootstrap snapshot is necessary for incremental backups with 'btrfs send -p'.
    """
    src_bootstrap = src_dir.joinpath(_btrfs_bootstrap_snapshot_basename())
    dst_bootstrap = dst_dir.with_path(dst_dir.path.joinpath(_btrfs_bootstrap_snapshot_basename()))
    src_bootstrap_exists = src_bootstrap.is_dir()
    dst_bootstrap_exists = dst_bootstrap.is_dir()
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
        pass  # already bootstrapped
    return src_bootstrap


def _btrfs_bootstrap_remote_to_local(src_dir: SSHTarget, dst_dir: Path) -> SSHTarget:
    """Perform the bootstrap phase of a remote-to-local backup.

    The bootstrap snapshot is necessary for incremental backups with 'btrfs send -p'.
    """
    src_bootstrap = src_dir.with_path(src_dir.path.joinpath(_btrfs_bootstrap_snapshot_basename()))
    dst_bootstrap = dst_dir.joinpath(_btrfs_bootstrap_snapshot_basename())
    src_bootstrap_exists = src_bootstrap.is_dir()
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
        pass  # already bootstrapped
    return src_bootstrap


def check_btrfs_filesystem_local(path: Path, label: str) -> list[str]:
    if not path.is_dir():
        return []
    p = subprocess.run(
        ["stat", "-f", "-c", "%T", str(path)],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )
    if p.returncode != 0 or p.stdout.strip() != "btrfs":
        return [f"{label} is not on a btrfs filesystem: {path}"]
    return []


def check_btrfs_filesystem_remote(sshtarget: SSHTarget, label: str) -> list[str]:
    p = subprocess.run(
        sshtarget.openssh_cmd(f"stat -f -c %T '{sshtarget.path}'"),
        check=False,
        capture_output=True,
        encoding="utf-8",
    )
    if p.returncode != 0 or p.stdout.strip() != "btrfs":
        return [
            f"{label} is not on a btrfs filesystem on remote {sshtarget.host}: {sshtarget.path}"
        ]
    return []
