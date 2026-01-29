import subprocess
from pathlib import Path
from shutil import rmtree
import voluptuous as vlp

import yaesm.backup as bckp
from yaesm.sshtarget import SSHTarget
from yaesm.timeframe import Timeframe
from yaesm.backend.backendbase import BackendBase

class RsyncBackend(BackendBase):
    """The rysnc backup execution backend. See BackendBase for more details on
    backup execution backends in general.
    """
    def config_schema() -> vlp.Schema:
        """Rsync backups allow user to specify arbitrary extra options via a
        'rsync_extra_opts' setting. This setting can associate to a string
        containing the options, or a list of strings containing the options. In
        either case the value is promoted to a list of string split on whitespace.
        The 'rsync_extra_opts' key is renamed to 'extra_opts' in the outputted dict.
        """
        def _promote_options_to_list_of_strings(d: dict) -> dict:
            if "rsync_extra_opts" in d:
                opts = d["rsync_extra_opts"]
                if isinstance(opts, str):
                    d["rsync_extra_opts"] = opts.split()
                elif isinstance(opts, list):
                    d["rsync_extra_opts"] = [word for opt in opts for word in opt.split()]
            return d

        def _rename_key_extra_opts(d:dict) -> dict:
            if "rsync_extra_opts" in d:
                d["extra_opts"] = d["rsync_extra_opts"]
                del d["rsync_extra_opts"]
            return d

        return vlp.Schema(vlp.All(
            { vlp.Optional("rsync_extra_opts"): vlp.Any(str, [str]) },
            _promote_options_to_list_of_strings,
            _rename_key_extra_opts),
            extra=vlp.ALLOW_EXTRA
        )

    def _exec_backup_local_to_local(self, backup:bckp.Backup, backup_basename:str, timeframe:Timeframe):
        return self._exec_backup(backup, backup_basename, timeframe)

    def _exec_backup_local_to_remote(self, backup:bckp.Backup, backup_basename:str, timeframe:Timeframe):
        return self._exec_backup(backup, backup_basename, timeframe)

    def _exec_backup_remote_to_local(self, backup:bckp.Backup, backup_basename:str, timeframe:Timeframe):
        return self._exec_backup(backup, backup_basename, timeframe)

    def _delete_backups_local(self, *backups):
        for backup in backups:
            rmtree(backup)
        return backups

    def _delete_backups_remote(self, *backups):
        """Note that the remote user must have passwordless sudo access to rm.
        Also note that all the backups in `*backups` are assumed to be SSHTarget's
        all at the same host.
        """
        for backup in backups:
            subprocess.run(backup.openssh_cmd(f"sudo -n rm -r -f '{backup.path}'"), check=True)
        return backups
        
    def _exec_backup(self, backup:bckp.Backup, backup_basename:str, timeframe:Timeframe):
        """
        Execute a single backup for `backup` in the timeframe `timeframe`. This
        function automatically deals with if the backup is local-to-local,
        local-to-remote, or remote-to-local. If existing backups for this backup
        already exist, then the latest one is used with rsync's --link-dest
        option, which allows for incremental backups.
        """
        rsync_cmd = ["rsync", "--verbose", "--archive", "--numeric-ids", "--delete"]
        if self.extra_opts:
            rsync_cmd += backup.extra_opts

        backups = bckp.backups_collect(backup) # note that we dont pass timeframe here
        if backups:
            newest_backup = backups[0]
            if isinstance(newest_backup, SSHTarget):
                newest_backup = newest_backup.path
            rsync_cmd += [f"--link-dest={newest_backup}"]

        if backup.backup_type == "local_to_remote":
            rsync_cmd += ["-e", "ssh " + backup.dst_dir.openssh_opts(string=True)]
            dst_dir = _rsync_translate_sshtarget(backup.dst_dir)
        else:
            dst_dir = backup.dst_dir

        if backup.backup_type == "remote_to_local":
            rsync_cmd += ["-e", "ssh " + backup.src_dir.openssh_opts(string=True)]
            src_dir = _rsync_translate_sshtarget(backup.src_dir)
        else:
            src_dir = backup.src_dir
    
        dst_dir = Path(dst_dir).joinpath(backup_basename)
        rsync_cmd += [f"{src_dir}/", f"{dst_dir}/"]
    
        subprocess.run(rsync_cmd, check=True)

        if backup.backup_type == "local_to_remote":
            return backup.dst_dir.with_path(dst_dir)
        else:
            return dst_dir
    
def _rsync_translate_sshtarget(sshtarget):
    user = "" if sshtarget.user is None else f"{sshtarget.user}@"
    return f"{user}{sshtarget.host}:{sshtarget.path}"
