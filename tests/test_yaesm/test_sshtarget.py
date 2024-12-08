import pytest
from yaesm.sshtarget import SSHTarget, SSHTargetException
import subprocess
from pathlib import Path

def test_sshtarget_constructor():
    key = Path("/a/path/to/a/key")

    target = SSHTarget(f"ssh://p2222:larry@localhost:/a/random/path", key)
    assert target.port == 2222
    assert target.user == "larry"
    assert target.host == "localhost"
    assert target.path == Path("/a/random/path")
    assert target.key  == key

    # port specification is optional
    target = SSHTarget(f"ssh://larry@localhost:/a/random/path", key)
    assert target.port == None
    assert target.user == "larry"
    assert target.host == "localhost"
    assert target.path == Path("/a/random/path")
    assert target.key  == key

    # port specification is optional
    target = SSHTarget(f"ssh://patrickhost:/a/random/path", key)
    assert target.port == None
    assert target.user == None
    assert target.host == "patrickhost"
    assert target.path == Path("/a/random/path")
    assert target.key  == key

    # port specification is optional
    target = SSHTarget(f"ssh://p4444:larryhost:/a/random/path", key)
    assert target.port == 4444
    assert target.user == None
    assert target.host == "larryhost"
    assert target.path == Path("/a/random/path")
    assert target.key  == key

def test_openssh_cmd(sshtarget):
    p = subprocess.run(sshtarget.openssh_cmd("whoami && printf '%s\\n' foo 1>&2 && exit 73"), shell=True, capture_output=True, encoding="utf-8")
    returncode = p.returncode
    stdout = p.stdout
    stderr = p.stderr
    assert returncode == 73
    assert stdout == f"{sshtarget.user}\n"
    assert stderr == "foo\n"

    openssh_cmd = sshtarget.openssh_cmd("printf '%s\\n' foo && printf '%s\\n' bar && printf '%s\\n' baz && 1>&2 printf '%s\\n' quux")
    p = subprocess.run(f"{openssh_cmd} | grep ba; exit 42", shell=True, capture_output=True, encoding="utf-8")
    returncode = p.returncode
    stdout = p.stdout
    stderr = p.stderr
    assert returncode == 42
    assert stdout == "bar\nbaz\n"
    assert stderr == "quux\n"

def test_with_path(sshtarget):
    new_sshtarget = sshtarget.with_path(Path("/foo"))
    assert new_sshtarget.path == Path("/foo")
    assert new_sshtarget.user == sshtarget.user
    assert new_sshtarget.host == sshtarget.host
    assert new_sshtarget.key == sshtarget.key
