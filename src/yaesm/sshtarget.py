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
    used for authentication to the server. Optionally, you can also pass an
    sshconfig which points to an existing file that will be passed to all ssh
    commands via the ssh '-F' flag.

    Example::
        sshtarget = SSHTarget("ssh://p22:fred@fredserver:/backups", Path("/home/larry/.ssh/id_rsa"))
        sshtarget = SSHTarget("ssh://fredhost:/backups, Path("/home/larry/.ssh/id_rsa"), sshconfig=Path("/home/larry/.ssh/larrys_ssh_config"))
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
        target_re = re.compile("^ssh://(p[0-9]+:)?([^:]+):(/.*)$")
        result = target_re.match(spec)
        return result

    def with_path(self, path:Path):
        """Returns a copy of 'self' (via copy.deepcopy()) but with path 'path'."""
        sshtarget = copy.deepcopy(self)
        sshtarget.path = Path(path)
        return sshtarget

    def openssh_opts(self, string=False):
        """Returns an exec list (`string=False`) or a string (`string=True`)
        containing OpenSSH options to enforce key-based authentication, ssh
        multiplexing, and strict host-key checking. Also ensures the proper port
        and configuration file is used.
        """
        configfile_opt = [] if self.sshconfig is None else ["-F", self.sshconfig]
        port_opt = [] if self.port is None else ["-p", str(self.port)]
        default_opts = [
            "-q",
            "-i", self.key,
            "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "PasswordAuthentication=no",
            "-o", "ControlMaster=auto",
            "-o", "ControlPath=~/.ssh/yaesm-controlmaster-%r@%h:%p",
            "-o", "ControlPersist=310"
        ]
        opts = [*configfile_opt, *port_opt, *default_opts]
        if string:
            opts = " ".join([shlex.quote(str(opt)) for opt in opts])
        return opts

    def openssh_cmd(self, cmd, string=False):
        """Returns an exec list (`string=False`) or a string (`string=True`) of
        an OpenSSH command that executes 'cmd' on the SSHTargets remote server.
        See 'openssh_opts()' for details on the OpenSSH options that are used.

        Example usage::
            p = subprocess.run(sshtarget.openssh_cmd("btrfs send /home/fred/snapshots/snapshot12", string=True) + " | " + "btrfs receive /fred-home-backups/", shell=True, check=True, capture_output=True, encoding="utf-8")
        """
        host = self.host if self.user is None else f"{self.user}@{self.host}"
        cmd = ["ssh", *self.openssh_opts(), host, cmd]
        if string:
            cmd = " ".join([shlex.quote(str(opt)) for opt in cmd])
        return cmd

    def can_connect(self):
        """Return True if we can establish a connection to the SSH target server
        and return False otherwise.
        """
        return 0 == subprocess.run(self.openssh_cmd("exit 0")).returncode

    def is_dir(self, d=None):
        """Return True if 'd' is an existing directory on the remote SSH server.
        If 'd' is None then default to checking 'self.path'.
        """
        if d is None:
            d = self.path
        return 0 == subprocess.run(self.openssh_cmd(f"[ -d '{d}' ]; exit $?")).returncode

    def is_file(self, f=None):
        """Return True if 'f' is an existing file on the remote SSH server. If
        'f' is None then default to checking 'self.path'
        """
        if f is None:
            f = self.path
        return 0 == subprocess.run(self.openssh_cmd(f"[ -f '{f}' ]; exit $?")).returncode

    def mkdir(self, d=None, parents=False, check=True):
        """Mkdir the directory 'd' on the remote SSH server. If 'd' is None,
        then default to 'self.path'. If 'parents' is True then use the mkdir
        '-p' flag. The 'check' arg is passed along to subprocess.run(). Return
        True if the mkdir command succeeded, otherwise return False.
        """
        if d is None:
            d = self.path
        p_flag = "-p" if parents else ""
        return 0 == subprocess.run(self.openssh_cmd(f"if ! [ -d '{d}' ]; then mkdir {p_flag} '{d}'; fi"), check=check).returncode

    def touch(self, f=None, check=True):
        """Touch the file 'f' on the remote SSH server. If 'f' is None then default
        to 'self.path'. The 'check' arg is passed along to subprocess.run(). Return
        True if the touch command succeeded, otherwise return False.
        """
        if f is None:
            f = self.path
        return 0 == subprocess.run(self.openssh_cmd(f"touch '{f}'"), check=check).returncode
