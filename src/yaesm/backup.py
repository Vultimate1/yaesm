"""src/yaesm/backup.py"""

import os
import re
import subprocess
import dataclasses
from datetime import datetime
from pathlib import Path

from yaesm.sshtarget import SSHTarget
from yaesm.timeframe import Timeframe

class BackupError(Exception):
    ...

@dataclasses.dataclass
class Backup:
    def __init__(self, name, backend, src_dir, dst_dir, timeframes):
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
        else: # remote_to_remote
            raise BackupError(f"backup {self.name} has both src_dir and dst_dir as ssh targets")

def backup_name_valid(backup_name:str) -> bool:
    """Return True if `backup_name` is a valid backup name, otherwise return False."""
    return bool(re.match("^[a-z][-_:@a-z0-9]*$", backup_name, re.IGNORECASE))

def backup_basename_re(backup=None, timeframe=None):
    """Returns a re compiled regex to match a yaesm backup basename. If `backup`
    is given, then only match a basename for `backup`. If `timeframe` is given,
    then only match a basename for `timeframe`.
    """
    backup_name_re_component = ".+" if backup is None else backup.name
    timeframe_name_re_component = ".+" if timeframe is None else timeframe.name
    return re.compile(
        f"^yaesm-({backup_name_re_component})-({timeframe_name_re_component})" +
        "\\.([0-9]{4})_([0-9]{2})_([0-9]{2})_([0-9]{2}):([0-9]{2})$"
    )

def backup_basename_update_time(backup_basename):
    re_result = backup_basename_re().match(backup_basename)
    backup_name = re_result.group(1)
    timeframe_name = re_result.group(2)
    datetime_now = datetime.now()
    name = datetime_now.strftime(f"yaesm-{backup_name}-{timeframe_name}.%Y_%m_%d_%H:%M")
    return name

def backup_basename_now(backup:Backup, timeframe:Timeframe):
    """Return the basename of a yaesm backup for the current time"""
    datetime_now = datetime.now()
    name = datetime_now.strftime(f"yaesm-{backup.name}-{timeframe.name}.%Y_%m_%d_%H:%M")
    return name

def backup_to_datetime(backup):
    """Construct and return a datetime object based on the basename of a yaesm backup.
    This function accepts either a Path to a backup, the basename of a backup,
    or an SSHTarget for a backup.
    """
    if isinstance(backup, SSHTarget):
        backup_basename = os.path.basename(backup.path)
    else:
        backup_basename = os.path.basename(backup)
    backup_basename_re_match = backup_basename_re().match(backup_basename)
    year, month, day, hour, minute = backup_basename_re_match.group(3, 4, 5, 6, 7)
    dt = datetime.strptime(f"{year}_{month}_{day}_{hour}:{minute}", "%Y_%m_%d_%H:%M")
    return dt

def backups_sorted(backups):
    """Returns list of backups (paths, basenames, or SSHTargets) sorted from newest to oldest."""
    sorted_backups = sorted(backups, key=backup_to_datetime, reverse=True)
    return sorted_backups

def backups_collect(backup, timeframe=None):
    """This function collects all the yaesm backups for the Backup `backup`.
    If the Timeframe `timeframe` is given, then only collect the backups in this
    given Timeframe. Remember that all the backups for all the timeframes are
    stored in the same directory.
    """
    backups = []
    backup_basename_re_ = backup_basename_re(backup=backup, timeframe=timeframe)
    if backup.backup_type == "local_to_remote":
        sshtarget = backup.dst_dir
        collect_sh_cmd = \
f"""
for f in $(ls -1 '{sshtarget.path}'); do
  if [ -d \"{sshtarget.path}/$f\" ]; then
    printf '%s/%s\\n' '{sshtarget.path}' \"$f\";
  fi
done"""
        p = subprocess.run(sshtarget.openssh_cmd(collect_sh_cmd), shell=True, check=True,
                           capture_output=True, encoding="utf-8")
        for bkp in p.stdout.splitlines():
            bkp = Path(bkp)
            if backup_basename_re_.match(bkp.name):
                backups.append(sshtarget.with_path(bkp))
    else:
        dst_dir = backup.dst_dir
        for path in dst_dir.iterdir():
            if path.is_dir() and backup_basename_re_.match(path.name):
                backups.append(path)
    backups = backups_sorted(backups)
    return backups
