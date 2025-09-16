import re
import shlex
import subprocess
import copy
from pathlib import Path

class SSHTargetException(Exception):
    ...

class SSHTarget:
    """The SSHTarget class manages connections to SSH servers using openssh.
    An SSHTarget is defined by its "target spec" which is a string of the form
    ssh://p$PORT:$HOST:$PATH. $HOST can either be a host defined in a
    .ssh/config file, or can be a host specification of the form $USER@$HOST.
    The p$PORT: token is optional. To initialize a SSHTarget you must pass the
    constructor both a target spec, and the path to a private key that will be
    used for authentication to the server.

    Example::
        sshtarget = SSHTarget("ssh://p22:fred@fredserver:/backups", Path("/home/larry/.ssh/id_rsa"))
        sshtarget = SSHTarget("ssh://fredhost:/backups, Path("/home/larry/.ssh/id_rsa"))
    """
    def __init__(self, target_spec, key:Path, sshconfig=None):
        self.key = Path(key)
        self.sshconfig = sshconfig
        user_host_re = re.compile("^([^@]+)@(.+)$")
        if target_spec_re_result := self.is_sshtarget_spec(target_spec):
            self.spec = target_spec
            port = target_spec_re_result.group(1)
            self.port = None if port is None else int(port[1:-1]) # strip off leading 'p' and trailing ':' from 'port'
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
    def is_sshtarget_spec(spec:str) -> (re.Match[str] | None):
        """Check if `spec` is a valid ssh target spec."""
        if not isinstance(spec, str):
            return None
        target_re = re.compile("^ssh://(p[0-9]+:)?([^:]+):(.+)$")
        result = target_re.match(spec)
        return result

    def with_path(self, path:Path):
        """Returns a copy of 'self' (via copy.deepcopy()) but with path 'path'."""
        sshtarget = copy.deepcopy(self)
        sshtarget.path = Path(path)
        return sshtarget

    def openssh_opts(self, extra_opts=""):
        """Returns a string containing OpenSSH options to enforce key-based
        authentication, ssh multiplexing, and strict host-key checking. Also
        ensures the proper port and configuration file is used. Extra OpenSSH
        options can be added by setting 'extra_opts' to a string containing
        OpenSSH options.
        """
        configfile_opt = "" if self.sshconfig is None else f"-F '{self.sshconfig}'"
        port_opt = "" if self.port is None else f"-p {self.port}"
        return f"{extra_opts} -q -i '{self.key}' -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o ControlMaster=auto -o 'ControlPath=~/.ssh/yaesm-controlmaster-%r@%h:%p' -o ControlPersist=310 {configfile_opt} {port_opt}"

    def openssh_cmd(self, cmd, extra_opts="", quote_cmd=True):
        """Returns a string of an OpenSSH command that executes 'cmd' on the
        SSHTargets remote server. See 'openssh_opts()' for details on the OpenSSH
        options that are used.

        If 'quote_cmd' is true then the 'cmd' arg is quoted with shlex.quote().

        The caller can pass extra openssh opts by setting 'extra_opts' to a string
        containing OpenSSH options.

        Example usage::
            p = subprocess.run(sshtarget.openssh_cmd("btrfs send /home/fred/snapshots/snapshot12") + " | " + "btrfs receive /fred-home-backups/", shell=True, check=True, capture_output=True, encoding="utf-8")
        """
        if quote_cmd:
            cmd = shlex.quote(cmd)
        host = self.host if self.user is None else f"{self.user}@{self.host}"
        return f"ssh {self.openssh_opts(extra_opts)} '{host}' {cmd}"

    def is_dir(self, d=None):
        """Return True if 'd' is an existing directory on the remote SSH server.
        If 'd' is None then default to checking 'self.path'."""
        if d is None:
            d = self.path
        return 0 == subprocess.run(self.openssh_cmd(f"[ -d '{d}' ]; exit $?"), shell=True).returncode

    def is_file(self, f=None):
        """Return True if 'f' is an existing file on the remote SSH server. If
        'f' is None then default to checking 'self.path'"""
        if f is None:
            f = self.path
        return 0 == subprocess.run(self.openssh_cmd(f"[ -f '{f}' ]; exit $?"), shell=True).returncode

    def mkdir(self, d=None, parents=False, check=True):
        """Mkdir the directory 'd' on the remote SSH server. If 'd' is None,
        then default to 'self.path'. If 'parents' is True then use the mkdir
        '-p' flag. The 'check' arg is passed along to subprocess.run(). Return
        True if the mkdir command succeeded, otherwise return False.
        """
        if d is None:
            d = self.path
        p_flag = "-p" if parents else ""
        return 0 == subprocess.run(self.openssh_cmd(f"if ! [ -d '{d}' ]; then mkdir {p_flag} '{d}'; fi"), shell=True, check=check).returncode

    def touch(self, f=None, check=True):
        """Touch the file 'f' on the remote SSH server. If 'f' is None then default
        to 'self.path'. The 'check' arg is passed along to subprocess.run(). Return
        True if the touch command succeeded, otherwise return False.
        """
        if f is None:
            f = self.path
        return 0 == subprocess.run(self.openssh_cmd(f"touch '{f}'"), shell=True, check=check).returncode
