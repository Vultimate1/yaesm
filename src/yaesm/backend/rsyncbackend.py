"""src/yaesm/backend/rsyncbackend.py."""

import subprocess
from pathlib import Path
from shutil import rmtree

import voluptuous as vlp

import yaesm.backup as bckp
from yaesm.backend.backendbase import PathBackendBase
from yaesm.sshtarget import SSHTarget
from yaesm.timeframe import Timeframe


class RsyncBackend(PathBackendBase):
    """The rsync backup execution backend."""

    @staticmethod
    def config_settings() -> set[str]:
        return {"rsync_extra_opts"}

    @staticmethod
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

        def _rename_key_extra_opts(d: dict) -> dict:
            if "rsync_extra_opts" in d:
                d["extra_opts"] = d["rsync_extra_opts"]
                del d["rsync_extra_opts"]
            return d

        return vlp.Schema(
            vlp.All(
                {vlp.Optional("rsync_extra_opts"): vlp.Any(str, [str])},
                _promote_options_to_list_of_strings,
                _rename_key_extra_opts,
            ),
            extra=vlp.ALLOW_EXTRA,
        )

    def create(self, backup: bckp.Backup, timeframe: Timeframe, name: str) -> bckp.BackupArtifact:
        locator = self._exec_backup(backup, name, timeframe)
        path = locator.path if isinstance(locator, SSHTarget) else locator
        return bckp.BackupArtifact(name, timeframe.name, bckp.backup_to_datetime(name), str(path))

    def delete(self, backup: bckp.Backup, artifacts: list[bckp.BackupArtifact]) -> None:
        if isinstance(backup.dst_dir, SSHTarget):
            for artifact in artifacts:
                path = Path(artifact.locator)
                subprocess.run(
                    backup.dst_dir.openssh_cmd(["rm", "-r", "-f", "--", path]), check=True
                )
        else:
            for artifact in artifacts:
                rmtree(artifact.locator)

    def _exec_backup(
        self, backup: bckp.Backup, backup_basename: str, timeframe: Timeframe
    ) -> Path | SSHTarget:
        """Execute a single backup for `backup` in the timeframe `timeframe`. This
        function automatically deals with if the backup is local-to-local,
        local-to-remote, or remote-to-local. If existing backups for this backup
        already exist, then the latest one is used with rsync's --link-dest
        option, which allows for incremental backups.
        """
        rsync_cmd = ["rsync", "--verbose", "--archive", "--numeric-ids", "--delete"]
        if self.extra_opts:
            rsync_cmd += self.extra_opts

        backups = self.collect(backup)  # note that we dont pass timeframe here
        if backups:
            rsync_cmd += [f"--link-dest={backups[0].locator}"]

        if isinstance(backup.dst_dir, SSHTarget):
            rsync_cmd += ["-e", "ssh " + backup.dst_dir.openssh_opts(string=True)]
            dst_dir = Path(_rsync_translate_sshtarget(backup.dst_dir)).joinpath(backup_basename)
        else:
            dst_dir = backup.dst_dir.joinpath(backup_basename)

        if isinstance(backup.src_dir, SSHTarget):
            rsync_cmd += ["-e", "ssh " + backup.src_dir.openssh_opts(string=True)]
            src_dir: str | Path = _rsync_translate_sshtarget(backup.src_dir)
        else:
            src_dir = backup.src_dir

        rsync_cmd += [f"{src_dir}/", f"{dst_dir}/"]

        try:
            subprocess.run(rsync_cmd, check=True)
        except subprocess.CalledProcessError:
            if not isinstance(backup.dst_dir, SSHTarget) and dst_dir.exists():
                rmtree(dst_dir)
            raise

        if isinstance(backup.dst_dir, SSHTarget):
            return backup.dst_dir.with_path(backup.dst_dir.path.joinpath(backup_basename))
        return dst_dir


def _rsync_translate_sshtarget(sshtarget: SSHTarget) -> str:
    user = "" if sshtarget.user is None else f"{sshtarget.user}@"
    return f"{user}{sshtarget.host}:{sshtarget.path}"
