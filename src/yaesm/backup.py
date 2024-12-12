import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from yaesm.sshtarget import SSHTarget

class BackupError(Exception):
    ...

class Backup:
    def __init__(self, name, src_dir, dst_dir, timeframes):
        self.name = name
        self.src_dir = src_dir
        self.dst_dir = dst_dir
        self.timeframes = timeframes
        self._src_is_sshtarget = isinstance(self.src_dir, SSHTarget)
        self._dst_is_sshtarget = isinstance(self.dst_dir, SSHTarget)

        if not(self._src_is_sshtarget) and not(self._dst_is_sshtarget):
            self.backup_type = "local_to_local"
        elif not(self._src_is_sshtarget) and self._dst_is_sshtarget:
            self.backup_type = "local_to_remote"
        elif self._src_is_sshtarget and not(self._dst_is_sshtarget):
            self.backup_type = "remote_to_local"
        else: # remote_to_remote
            raise BackupError(f"backup {self.name} has both src_dir and dst_dir as ssh targets")

def backup_basename_re():
    """Returns a re compiled regex to match a yaesm backup basename."""
    return re.compile("^yaesm-backup@[0-9]{4}_[0-9]{2}_[0-9]{2}_[0-9]{2}:[0-9]{2}$")

def backup_basename_now():
    """Return the basename of a yaesm backup for the current time."""
    datetime_now = datetime.now()
    name = datetime_now.strftime("yaesm-backup@%Y_%m_%d_%H:%M")
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
    dt = datetime.strptime(backup_basename, "yaesm-backup@%Y_%m_%d_%H:%M")
    return dt

def backups_sorted(backups):
    """Returns list of backups (paths, basenames, or SSHTargets) sorted from newest to oldest."""
    backups_sorted = sorted(backups, key=backup_to_datetime, reverse=True)
    return backups_sorted

def backups_collect(target):
    """This function collects all the yaesm backups at 'target', which can
    either be an SSHTarget or a Path. If 'target' is a Path then return a list
    of Paths of all the yaesm backups on the local system at path 'target'. If
    'target' is a SSHTarget, then return a list of all the yaesm backups as
    SSHTargets at the remote 'target.path'.
    """
    backups = []
    if isinstance(target, SSHTarget):
        cmd = f"for f in $(ls -1 '{target.path}'); do if [ -d \"{target.path}/$f\" ]; then printf '%s/%s\\n' '{target.path}' \"$f\"; fi done"
        p = subprocess.run(target.openssh_cmd(cmd), shell=True, check=True, capture_output=True, encoding="utf-8")
        for backup in p.stdout.splitlines():
            backup = Path(backup)
            if backup_basename_re().match(backup.name):
                backups.append(target.with_path(backup))
    else: # target is a Path
        for path in target.iterdir():
            basename = path.name
            if path.is_dir() and backup_basename_re().match(basename):
                backups.append(path)
    backups = backups_sorted(backups)
    return backups
