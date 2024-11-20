import pytest
from yaesm.sshtarget import SSHTarget, SSHTargetException
from pathlib import Path

@pytest.fixture
def sshtarget_generator(localhost_server_generator):
    """Fixture for generating SSHTargets for mock localhost ssh servers. Note
    that the target path is the home directory of the localhost ssh server user.
    """
    def generator():
        localhost_server = localhost_server_generator()
        user = localhost_server["user"]
        key = localhost_server["key"]
        target_spec = f"ssh://p22:{user.pw_name}@localhost:{user.pw_dir}"
        sshtarget = SSHTarget(target_spec, key)
        return sshtarget
    return generator

@pytest.fixture
def sshtarget(sshtarget_generator):
    """Fixture for generating a single SSHTarget on a mock localhost ssh server.
    See the sshtarget_generator fixture for more information.
    """
    return sshtarget_generator()

def test_sshtarget_constructor(localhost_server):
    user = localhost_server["user"]
    key  = localhost_server["key"]

    with pytest.raises(SSHTargetException):
        SSHTarget("INVALID_SSHTARGET_SPEC", key)

    target = SSHTarget(f"ssh://p2222:{user.pw_name}@localhost:/a/random/path", key)
    assert target.port == 2222
    assert target.user == user.pw_name
    assert target.host == "localhost"
    assert target.path == Path("/a/random/path")
    assert target.key  == key

def test_sshtarget_connection_and_command_execution(sshtarget):
    returncode, stdout_str, stderr_str = sshtarget.exec_command("whoami && echo foo 1>&2 && exit 12")
    assert returncode == 12
    assert stdout_str == f"{sshtarget.user}\n"
    assert stderr_str == "foo\n"

    stdin, stdout, stderr = sshtarget.exec_command("echo foo && echo bar 1>&2", return_files=True)
    assert stdout.read().decode("utf-8") == "foo\n"
    assert stderr.read().decode("utf-8") == "bar\n"
