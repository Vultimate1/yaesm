"""src/yaesm/sshtarget.py."""

from __future__ import annotations

import copy
import re
import shlex
import subprocess
from pathlib import Path

import yaesm.ty as ty


class SSHTargetException(Exception): ...


class SSHTarget:
    """The SSHTarget class manages connections to SSH servers using openssh.
    An SSHTarget is defined by its "target spec" which is a string of the form
    'ssh://p$PORT:$HOST:$PATH'. '$HOST' can either be a host defined in a
    .ssh/config file, or can be a host specification of the form '$USER@$HOST'.
    The 'p$PORT:' token is optional. To initialize a SSHTarget you must pass the
    constructor both a target spec, and the path to a private key that will be
    used for authentication to the server. Optionally, you can also pass an
    sshconfig which points to an existing file that will be passed to all ssh
    commands via the ssh '-F' flag.

    Example::
        sshtarget = SSHTarget("ssh://p22:fred@fredserver:/backups", Path("/home/larry/.ssh/id_rsa"))
        sshtarget = SSHTarget("ssh://fredhost:/backups, Path("/home/larry/.ssh/id_rsa"), \
            sshconfig=Path("/home/larry/.ssh/larrys_ssh_config"))
    """

    def __init__(self, target_spec: str, key: Path, sshconfig: Path | None = None) -> None:
        self.key = Path(key)
        self.sshconfig = sshconfig
        user_host_re = re.compile("^([^@]+)@(.+)$")
        if target_spec_re_result := self.is_sshtarget_spec(target_spec):
            self.spec = target_spec
            port = target_spec_re_result.group(1)
            # Strip off leading 'p' and trailing ':' from 'port'
            self.port = None if port is None else int(port[1:-1])
            self.host = target_spec_re_result.group(2)
            self.path = Path(target_spec_re_result.group(3))
            if user_host_re_result := user_host_re.match(self.host):
                self.user = user_host_re_result.group(1)
                self.host = user_host_re_result.group(2)
            else:
                self.user = None
        else:
            raise SSHTargetException(f"invalid SSHTarget spec: {target_spec}")

    @staticmethod
    def is_sshtarget_spec(spec: str) -> re.Match[str] | None:
        """Check if `spec` is a valid ssh target spec."""
        if not isinstance(spec, str):
            return None
        target_re = re.compile("^ssh://(p[0-9]+:)?([^:]+):(/.*)$")
        result = target_re.match(spec)
        return result

    def with_path(self, path: Path) -> SSHTarget:
        """Returns a copy of `self` (via `copy.deepcopy()`) but with Path `path`."""
        sshtarget = copy.deepcopy(self)
        sshtarget.path = Path(path)
        return sshtarget

    def __str__(self) -> str:
        port = "" if self.port is None else f"p{self.port}:"
        user = "" if self.user is None else f"{self.user}@"
        return f"ssh://{port}{user}{self.host}:{self.path}"

    @ty.overload
    def openssh_opts(self, string: ty.Literal[True]) -> str: ...

    @ty.overload
    def openssh_opts(self, string: ty.Literal[False] = ...) -> list[str | Path]: ...

    def openssh_opts(self, string: bool = False) -> list[str | Path] | str:
        """Returns an exec list (`string=False`) or a string (`string=True`)
        containing OpenSSH options to enforce key-based authentication, ssh
        multiplexing, and strict host-key checking. Also ensures the proper port
        and configuration file is used.
        """
        configfile_opt = [] if self.sshconfig is None else ["-F", self.sshconfig]
        port_opt = [] if self.port is None else ["-p", str(self.port)]
        default_opts = [
            "-o",
            f"IdentityFile={self.key}",
            "-o",
            "ControlMaster=auto",
            "-o",
            "ControlPath=~/.yaesm-ssh-controlmaster-%C",
            "-o",
            "ControlPersist=310",
            "-o",
            "RequestTTY=no",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=60",
            "-o",
            "ServerAliveInterval=60",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "ClearAllForwardings=yes",
            "-o",
            "ForwardAgent=no",
            "-o",
            "ForwardX11=no",
        ]
        opts: list[str | Path] = [*configfile_opt, *port_opt, *default_opts]
        if string:
            return " ".join([shlex.quote(str(opt)) for opt in opts])
        return opts

    @ty.overload
    def openssh_cmd(self, cmd: list[str | Path], string: ty.Literal[True]) -> str: ...

    @ty.overload
    def openssh_cmd(
        self, cmd: list[str | Path], string: ty.Literal[False] = ...
    ) -> list[str | Path]: ...

    def openssh_cmd(self, cmd: list[str | Path], string: bool = False) -> list[str | Path] | str:
        """Return an exec list (`string=False`) or string (`string=True`) for an
        OpenSSH command that safely quotes and executes `cmd` on the remote server.
        See `openssh_opts()` for details on the OpenSSH options that are used.

        Example usage::
            cmd = sshtarget.openssh_cmd(
                ["btrfs", "send", "/home/fred/snapshots/snapshot12"], string=True
            )
            p = subprocess.run(
                cmd + " | btrfs receive /fred-home-backups/",
                shell=True,
                check=True,
                capture_output=True,
                encoding="utf-8",
            )
        """
        host = self.host if self.user is None else f"{self.user}@{self.host}"
        remote_cmd = shlex.join(str(arg) for arg in cmd)
        parts: list[str | Path] = ["ssh", *self.openssh_opts(), host, remote_cmd]
        if string:
            return " ".join([shlex.quote(str(opt)) for opt in parts])
        return parts

    def can_connect(self) -> bool:
        """Return True if we can establish a connection to the SSH target server
        and return False otherwise.
        """
        return subprocess.run(self.openssh_cmd(["true"]), check=False).returncode == 0

    def exists(self, p: Path | None = None) -> bool:
        """Return True if `p` exists on the remote SSH server.
        If `p` is None then default to checking `self.path`.
        """
        if p is None:
            p = self.path
        return subprocess.run(self.openssh_cmd(["test", "-e", p]), check=False).returncode == 0

    def is_dir(self, d: Path | None = None) -> bool:
        """Return True if `d` is an existing directory on the remote SSH server.
        If `d` is None then default to checking `self.path`.
        """
        if d is None:
            d = self.path
        return subprocess.run(self.openssh_cmd(["test", "-d", d]), check=False).returncode == 0

    def is_file(self, f: Path | None = None) -> bool:
        """Return True if `f` is an existing file on the remote SSH server. If
        `f` is None then default to checking `self.path`.
        """
        if f is None:
            f = self.path
        return subprocess.run(self.openssh_cmd(["test", "-f", f]), check=False).returncode == 0

    def mkdir(self, d: Path | None = None, parents: bool = False, check: bool = True) -> bool:
        """Mkdir the directory `d` on the remote SSH server. If `d` is None,
        then default to `self.path`. If `parents` is True then use the mkdir
        '-p' flag. The `check` arg is passed along to `subprocess.run()`. Return
        True if the mkdir command succeeded, otherwise return False.
        """
        if d is None:
            d = self.path
        cmd: list[str | Path]
        if parents:
            cmd = ["mkdir", "-p", "--", d]
        else:
            cmd = ["sh", "-c", '[ -d "$1" ] || mkdir -- "$1"', "sh", d]
        return (
            subprocess.run(
                self.openssh_cmd(cmd),
                check=check,
            ).returncode
            == 0
        )

    def is_older_than(self, days: int, path: Path | None = None) -> bool:
        """Return whether a remote path is older than `days` days."""
        if path is None:
            path = self.path
        p = subprocess.run(
            self.openssh_cmd(["find", path, "-prune", "-mtime", f"+{days - 1}", "-print"]),
            check=True,
            capture_output=True,
            encoding="utf-8",
        )
        return bool(p.stdout)

    def touch(self, f: Path | None = None, check: bool = True) -> bool:
        """Touch the file `f` on the remote SSH server. If `f` is None then default
        to `self.path`. The `check` arg is passed along to `subprocess.run()`. Return
        True if the touch command succeeded, otherwise return False.
        """
        if f is None:
            f = self.path
        return subprocess.run(self.openssh_cmd(["touch", "--", f]), check=check).returncode == 0
