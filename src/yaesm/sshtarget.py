import paramiko
import re
import subprocess
from pathlib import Path

class SSHTargetException(Exception):
    ...

class SSHTarget:
    """The SSHTarget class manages connections to SSH servers using paramiko.
    An SSHTarget is defined by its "target spec" which is a string of the form
    ssh://p$PORT:$USER@HOST:$PATH. To initialize a SSHTarget you must pass the
    constructor both a target spec, and the path to a private key that will be
    used for authentication to the server.

    Example::
        sshtarget = SSHTarget("ssh://p22:fred@fredserver:/home/backups", Path("/home/larry/.ssh/id_rsa"))
        returncode, stdout, stderr = sshtarget.exec_command(f"ls -l {sshtarget.path}")
    """
    def __init__(self, target_spec, key:Path):
        sshtarget_re = re.compile("^ssh://p([0-9]+):([^@]+)@([^:]+):(.+)$")
        re_result = sshtarget_re.match(target_spec)
        if re_result:
            self.port = int(re_result.group(1))
            self.user = re_result.group(2)
            self.host = re_result.group(3)
            self.path = Path(re_result.group(4))
            self.key = key
            self._client = paramiko.SSHClient()
            self._client.set_missing_host_key_policy(paramiko.WarningPolicy)
        else:
            raise SSHTargetException(f"invalid SSHTarget spec: {target_spec}")

    def exec_command(self, command, return_files=False):
        """Execute 'command' on SSHTarget using paramiko.client.exec_command().
        Automatically establishes a connection using paramiko.client.connect()
        that authenticates with the SSHTarget key.

        If 'return_files' == False, then return a list containing
        [returncode, stdout_str, stderr_str], where stdout_str and stderr_str are
        utf-8 strings of the remote generated STDOUT and STDERR.

        Otherwise if 'return_files' == True, then return [stdin, stdout, stderr],
        where stdin, stdout, and stderr are Python file-like objects representing
        the commands STDIN, STDOUT, and STDERR (just like paramiko.exec_command()
        returns).

        Example::
            returncode, stdout, stderr = sshtarget.exec_command(f"ls -l {sshtarget.path}")
            stdin, stdout, stderr = sshtarget.exec_command(f"ls -l {sshtarget.path}", return_data=False)
        """
        if self._client.get_transport() is None or not self._client.get_transport().is_active():
            self._client.connect(
                self.host,
                username=self.user,
                port=self.port,
                key_filename=str(self.key),
                auth_timeout=60,
                timeout=None,
                allow_agent=False,
                look_for_keys=False
            )
        stdin, stdout, stderr = self._client.exec_command(command)
        if return_files:
            return [stdin, stdout, stderr]
        else:
            returncode = stdout.channel.recv_exit_status()
            stdout = stdout.read().decode("utf-8")
            stderr = stderr.read().decode("utf-8")
            return [returncode, stdout, stderr]
