"""src/yaesm/backend/rsyncbackend.py."""

import subprocess
from pathlib import Path
from shutil import rmtree

import voluptuous as vlp

import yaesm.backup as bckp
from yaesm.backend import pathsupport
from yaesm.backend.backendbase import BackendBase, CheckResult
from yaesm.sshtarget import SSHTarget
from yaesm.timeframe import Timeframe


class RsyncBackend(BackendBase):
    """The rsync backup execution backend."""

    src_dir: Path | SSHTarget
    dst_dir: Path | SSHTarget

    @staticmethod
    def config_settings() -> set[str]:
        return pathsupport.config_settings() | {"rsync_extra_opts"}

    @staticmethod
    def config_schema() -> vlp.Schema:
        """Rsync backups allow user to specify arbitrary extra options via a
        'rsync_extra_opts' setting. This setting can associate to a string
        containing the options, or a list of strings containing the options. In
        either case the value is promoted to a list of string split on whitespace.
        The options are stored on the backend instance.
        """

        def _promote_options_to_list_of_strings(d: dict) -> dict:
            if "rsync_extra_opts" in d:
                opts = d["rsync_extra_opts"]
                if isinstance(opts, str):
                    d["rsync_extra_opts"] = opts.split()
                elif isinstance(opts, list):
                    d["rsync_extra_opts"] = [word for opt in opts for word in opt.split()]
            return d

        def _apply_to_backend(d: dict) -> dict:
            if "rsync_extra_opts" in d:
                d["backend"].extra_opts = d.pop("rsync_extra_opts")
            return d

        return vlp.Schema(
            vlp.All(
                pathsupport.config_schema(),
                {vlp.Optional("rsync_extra_opts"): vlp.Any(str, [str])},
                _promote_options_to_list_of_strings,
                _apply_to_backend,
            ),
            extra=vlp.ALLOW_EXTRA,
        )

    @staticmethod
    def config_schema_extra() -> vlp.Schema:
        return pathsupport.config_schema_extra()

    def configure_paths(self, src_dir: Path | SSHTarget, dst_dir: Path | SSHTarget) -> None:
        pathsupport.configure_paths(self, src_dir, dst_dir)

    @property
    def backup_type(self) -> str:
        return pathsupport.backup_type(self)

    def format_locator(self, artifact: bckp.BackupArtifact) -> str:
        return pathsupport.format_locator(self, artifact)

    def check(self, backup: bckp.Backup) -> list[CheckResult]:
        return pathsupport.check(self)

    def collect(
        self, backup: bckp.Backup, timeframes: list[Timeframe] | None = None
    ) -> list[bckp.BackupArtifact]:
        return pathsupport.collect(self, backup, timeframes)

    def create(self, backup: bckp.Backup, timeframe: Timeframe, name: str) -> bckp.BackupArtifact:
        locator = self._exec_backup(backup, name, timeframe)
        path = locator.path if isinstance(locator, SSHTarget) else locator
        return bckp.BackupArtifact(name, timeframe.name, bckp.backup_to_datetime(name), str(path))

    def delete(self, backup: bckp.Backup, artifacts: list[bckp.BackupArtifact]) -> None:
        if isinstance(self.dst_dir, SSHTarget):
            for artifact in artifacts:
                path = Path(artifact.locator)
                subprocess.run(self.dst_dir.openssh_cmd(["rm", "-r", "-f", "--", path]), check=True)
        else:
            for artifact in artifacts:
                rmtree(artifact.locator)

    def _exec_backup(
        self, backup: bckp.Backup, backup_basename: str, timeframe: Timeframe
    ) -> Path | SSHTarget:
        """Execute a single backup for `backup` in the timeframe `timeframe`. This
        function automatically deals with if the backup is local-to-local,
        local-to-remote, remote-to-local, or remote-to-remote. If existing backups
        for this backup already exist, then the latest one is used with rsync's
        --link-dest option, which allows for incremental backups.
        """
        rsync_cmd: list[str | Path] = [
            "rsync",
            "--verbose",
            "--archive",
            "--numeric-ids",
            "--delete",
        ]
        if self.extra_opts:
            rsync_cmd += self.extra_opts

        backups = self.collect(backup)  # note that we dont pass timeframe here
        if backups:
            rsync_cmd += [f"--link-dest={backups[0].locator}"]

        if self.backup_type == "remote_to_remote":
            assert isinstance(self.src_dir, SSHTarget)
            assert isinstance(self.dst_dir, SSHTarget)
            dst_dir = self.dst_dir.path.joinpath(backup_basename)
            rsync_cmd += [f"{self.src_dir.path}/", f"{dst_dir}/"]
            subprocess.run(self.src_dir.openssh_cmd(rsync_cmd), check=True)
            return self.dst_dir.with_path(dst_dir)

        if isinstance(self.dst_dir, SSHTarget):
            rsync_cmd += ["-e", "ssh " + self.dst_dir.openssh_opts(string=True)]
            dst_dir = Path(_rsync_translate_sshtarget(self.dst_dir)).joinpath(backup_basename)
        else:
            dst_dir = self.dst_dir.joinpath(backup_basename)

        if isinstance(self.src_dir, SSHTarget):
            rsync_cmd += ["-e", "ssh " + self.src_dir.openssh_opts(string=True)]
            src_dir: str | Path = _rsync_translate_sshtarget(self.src_dir)
        else:
            src_dir = self.src_dir

        rsync_cmd += [f"{src_dir}/", f"{dst_dir}/"]

        try:
            subprocess.run(rsync_cmd, check=True)
        except subprocess.CalledProcessError:
            if not isinstance(self.dst_dir, SSHTarget) and dst_dir.exists():
                rmtree(dst_dir)
            raise

        if isinstance(self.dst_dir, SSHTarget):
            return self.dst_dir.with_path(self.dst_dir.path.joinpath(backup_basename))
        return dst_dir


def _rsync_translate_sshtarget(sshtarget: SSHTarget) -> str:
    user = "" if sshtarget.user is None else f"{sshtarget.user}@"
    return f"{user}{sshtarget.host}:{sshtarget.path}"
