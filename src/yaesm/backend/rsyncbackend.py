import subprocess
from pathlib import Path

import yaesm.backup as bckp
from yaesm.sshtarget import SSHTarget
from yaesm.timeframe import Timeframe
from yaesm.backend.backendbase import BackendBase

class RsyncBackend(BackendBase):
    """The rysnc backup execution backend. See BackendBase for more details on
    backup execution backends in general.
    """
    def _exec_backup_local_to_local(self, backup:bckp.Backup, timeframe:Timeframe):
        return self._exec_backup(backup, timeframe)

    def _exec_backup_local_to_remote(self, backup:bckp.Backup, timeframe:Timeframe):
        return self._exec_backup(backup, timeframe)

    def _exec_backup_remote_to_local(self, backup:bckp.Backup, timeframe:Timeframe):
        return self._exec_backup(backup, timeframe)

    def _delete_backups_local(self, *backups):
        subprocess.run(["rm", "-r", "-f", *backups], check=True)
        return backups

    def _delete_backups_remote(self, *backups):
        return backups # TODO: leverage the fact that we have sudo access to rsync
        
    def _exec_backup(self, backup:bckp.Backup, timeframe:Timeframe):
        rsync_cmd = ["rsync", "--verbose", "--archive", "--numeric-ids", "--delete", "--mkpath"]

        backups = bckp.backups_collect(backup) # note that we dont pass timeframe here
        if backups:
            newest_backup = backups[0]
            if isinstance(newest_backup, SSHTarget):
                newest_backup = newest_backup.path
            rsync_cmd += [f"--link-dest={newest_backup}"]

        if backup.backup_type == "local_to_remote":
            rsync_cmd += ["-e", "ssh " + backup.dst_dir.openssh_opts()]
            rsync_cmd += ["--rsync-path=sudo -n rsync"]
            dst_dir = _rsync_translate_sshtarget(backup.dst_dir)
        else:
            dst_dir = backup.dst_dir

        if backup.backup_type == "remote_to_local":
            rsync_cmd += ["-e", "ssh " + backup.src_dir.openssh_opts()]
            rsync_cmd += ["--rsync-path=sudo -n rsync"]
            src_dir = _rsync_translate_sshtarget(backup.src_dir)
        else:
            src_dir = backup.src_dir
    
        dst_dir = Path(dst_dir).joinpath(bckp.backup_basename_now(backup, timeframe))
        rsync_cmd += [f"{src_dir}/", f"{dst_dir}/"]
    
        subprocess.run(rsync_cmd, check=True)

        if backup.backup_type == "local_to_remote":
            return backup.dst_dir.with_path(dst_dir)
        else:
            return dst_dir
    
def _rsync_translate_sshtarget(sshtarget):
    user = "" if sshtarget.user is None else f"{sshtarget.user}@"
    return f"{user}{sshtarget.host}:{sshtarget.path}"
