"""src/yaesm/backup.py."""

import dataclasses
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

import yaesm.ty as ty
from yaesm.sshtarget import SSHTarget
from yaesm.timeframe import Timeframe


class BackupError(Exception): ...


@dataclasses.dataclass(frozen=True)
class BackupArtifact:
    """Backend-neutral backup metadata with a backend-defined locator."""

    name: str
    timeframe: str
    created_at: datetime
    locator: str


@dataclasses.dataclass
class Backup:
    def __init__(
        self,
        name: str,
        backend: ty.Any,
        src_dir: Path | SSHTarget,
        dst_dir: Path | SSHTarget,
        timeframes: list[Timeframe],
    ) -> None:
        self.name = name
        self.backend = backend
        self.src_dir = src_dir
        self.dst_dir = dst_dir
        self.timeframes = timeframes
        src_is_sshtarget = isinstance(self.src_dir, SSHTarget)
        dst_is_sshtarget = isinstance(self.dst_dir, SSHTarget)

        if not src_is_sshtarget and not dst_is_sshtarget:
            self.backup_type = "local_to_local"
        elif not src_is_sshtarget and dst_is_sshtarget:
            self.backup_type = "local_to_remote"
        elif src_is_sshtarget and not dst_is_sshtarget:
            self.backup_type = "remote_to_local"
        else:  # remote_to_remote
            raise BackupError(f"backup {self.name} has both src_dir and dst_dir as ssh targets")


def backup_name_valid(backup_name: str) -> bool:
    """Return True if `backup_name` is a valid backup name, otherwise return False."""
    return bool(re.match("^[a-z][-_:@a-z0-9]*$", backup_name, re.IGNORECASE))


def backup_basename_re(
    backup: Backup | None = None, timeframe: Timeframe | None = None
) -> ty.Pattern[str]:
    """Returns a re compiled regex to match a yaesm backup basename. If `backup`
    is given, then only match a basename for `backup`. If `timeframe` is given,
    then only match a basename for `timeframe`.
    """
    backup_name_re_component = ".+" if backup is None else backup.name
    timeframe_name_re_component = ".+" if timeframe is None else timeframe.name
    return re.compile(
        f"^yaesm-({backup_name_re_component})-({timeframe_name_re_component})"
        + "\\.([0-9]{4})_([0-9]{2})_([0-9]{2})_([0-9]{2}):([0-9]{2})$"
    )


def backup_basename_update_time(backup_basename: str) -> str:
    re_result = backup_basename_re().match(backup_basename)
    assert re_result is not None
    backup_name = re_result.group(1)
    timeframe_name = re_result.group(2)
    datetime_now = datetime.now()
    name = datetime_now.strftime(f"yaesm-{backup_name}-{timeframe_name}.%Y_%m_%d_%H:%M")
    return name


def backup_basename_now(backup: Backup, timeframe: Timeframe) -> str:
    """Return the basename of a yaesm backup for the current time."""
    datetime_now = datetime.now()
    name = datetime_now.strftime(f"yaesm-{backup.name}-{timeframe.name}.%Y_%m_%d_%H:%M")
    return name


def backup_to_datetime(backup: Path | str | SSHTarget) -> datetime:
    """Construct and return a datetime object based on the basename of a yaesm backup.
    This function accepts either a Path to a backup, the basename of a backup,
    or an SSHTarget for a backup.
    """
    if isinstance(backup, SSHTarget):
        backup_basename = os.path.basename(backup.path)
    else:
        backup_basename = os.path.basename(backup)
    backup_basename_re_match = backup_basename_re().match(backup_basename)
    assert backup_basename_re_match is not None
    year, month, day, hour, minute = backup_basename_re_match.group(3, 4, 5, 6, 7)
    dt = datetime.strptime(f"{year}_{month}_{day}_{hour}:{minute}", "%Y_%m_%d_%H:%M")
    return dt


def backups_sorted(
    backups: list[Path | str | SSHTarget],
) -> list[Path | str | SSHTarget]:
    """Returns list of backups (paths, basenames, or SSHTargets) sorted from newest to oldest."""
    sorted_backups = sorted(backups, key=backup_to_datetime, reverse=True)
    return sorted_backups


def backups_collect(
    backup: Backup, timeframes: list[Timeframe] | None = None
) -> list[BackupArtifact]:
    """Collect backup artifacts from newest to oldest."""
    return backup.backend.collect(backup, timeframes=timeframes)


def path_artifacts_collect(
    backup: Backup, timeframes: list[Timeframe] | None = None
) -> list[BackupArtifact]:
    """Collect directory backup artifacts sorted from newest to oldest.

    If `timeframes` is given, then only collect backups in those Timeframes.
    Remember that all the backups for all the timeframes are
    stored in the same directory.
    """
    artifacts: list[BackupArtifact] = []
    backup_basename_res = (
        [backup_basename_re(backup=backup)]
        if timeframes is None
        else [backup_basename_re(backup=backup, timeframe=timeframe) for timeframe in timeframes]
    )
    if isinstance(backup.dst_dir, SSHTarget):
        sshtarget = backup.dst_dir
        p = subprocess.run(
            sshtarget.openssh_cmd(
                [
                    "find",
                    sshtarget.path,
                    "-mindepth",
                    "1",
                    "-maxdepth",
                    "1",
                    "-type",
                    "d",
                    "-print0",
                ]
            ),
            check=True,
            capture_output=True,
            encoding="utf-8",
        )
        for bkp in p.stdout.split("\0"):
            if not bkp:
                continue
            bkp = Path(bkp)
            if any(pattern.match(bkp.name) for pattern in backup_basename_res):
                artifacts.append(_backup_artifact_from_path(backup, bkp))
    else:
        dst_dir = backup.dst_dir
        for path in dst_dir.iterdir():
            if path.is_dir() and any(pattern.match(path.name) for pattern in backup_basename_res):
                artifacts.append(_backup_artifact_from_path(backup, path))
    return sorted(artifacts, key=lambda artifact: artifact.created_at, reverse=True)


def _backup_artifact_from_path(backup: Backup, path: Path) -> BackupArtifact:
    match = backup_basename_re(backup=backup).match(path.name)
    assert match is not None
    return BackupArtifact(
        name=path.name,
        timeframe=match.group(2),
        created_at=backup_to_datetime(path),
        locator=str(path),
    )
