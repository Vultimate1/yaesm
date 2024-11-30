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
        target_spec_re = re.compile("^ssh://(p[0-9]+:)?([^:]+):(.+)$")
        user_host_re = re.compile("^([^@]+)@(.+)$")
        if target_spec_re_result := target_spec_re.match(target_spec):
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

    def openssh_cmd(self, cmd, extra_opts="", quote_cmd=True):
        """Returns a string of an openssh command that executes 'cmd' on the
        SSHTargets remote server. The returned openssh command enforces key-based
        auth, host key checking, and ssh multiplexing.

        If 'quote_cmd' is true then the 'cmd' arg is quoted with shlex.quote().

        The caller can pass extra openssh opts by setting 'extra_opts' to a string
        containing openssh options.

        Example usage::
            p = subprocess.run("set -o pipefail; " + sshtarget.openssh_cmd("btrfs send /home/fred") + " | btrfs receive /home-backups/yaesm-backup@1999_05_13_23:59", shell=True, check=True, capture_output=True, encoding="utf-8")
            stdout = p.stdout
            stderr = p.stderr
        """
        if quote_cmd:
            cmd = shlex.quote(cmd)
        host = self.host if self.user is None else f"{self.user}@{self.host}"
        configfile_opt = "" if self.sshconfig is None else f"-F '{self.sshconfig}'"
        port_opt = "" if self.port is None else f"-p {self.port}"
        return f"ssh {extra_opts} {configfile_opt} {port_opt} -q -i '{self.key}' -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o ControlMaster=auto -o 'ControlPath=~/.ssh/yaesm-controlmaster-%r@%h:%p' -o ControlPersist=310 '{host}' {cmd}"

    def with_path(self, path:Path):
        """Returns a copy of 'self' (via copy.deepcopy()) but with path 'path'."""
        sshtarget = copy.deepcopy(self)
        sshtarget.path = Path(path)
        return sshtarget
