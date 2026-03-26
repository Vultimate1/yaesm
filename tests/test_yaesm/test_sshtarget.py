"""tests/test_yaesm/test_sshtarget.py."""

import subprocess
from pathlib import Path

import pytest

from yaesm.sshtarget import SSHTarget


def test_sshtarget_constructor():
    key = Path("/a/path/to/a/key")

    target = SSHTarget("ssh://p2222:larry@localhost:/a/random/path", key)
    assert target.port == 2222
    assert target.user == "larry"
    assert target.host == "localhost"
    assert target.path == Path("/a/random/path")
    assert target.key == key

    # port specification is optional
    target = SSHTarget("ssh://larry@localhost:/a/random/path", key)
    assert target.port is None
    assert target.user == "larry"
    assert target.host == "localhost"
    assert target.path == Path("/a/random/path")
    assert target.key == key

    # port specification is optional
    target = SSHTarget("ssh://patrickhost:/a/random/path", key)
    assert target.port is None
    assert target.user is None
    assert target.host == "patrickhost"
    assert target.path == Path("/a/random/path")
    assert target.key == key

    # port specification is optional
    target = SSHTarget("ssh://p4444:larryhost:/a/random/path", key)
    assert target.port == 4444
    assert target.user is None
    assert target.host == "larryhost"
    assert target.path == Path("/a/random/path")
    assert target.key == key


def test_openssh_opts(sshtarget):
    opts = sshtarget.openssh_opts()
    assert isinstance(opts, list)
    opts = sshtarget.openssh_opts(string=True)
    assert isinstance(opts, str)
    opts = sshtarget.openssh_opts()
    assert len(opts)
    assert opts[0] != "ssh"
    assert "-i" in opts


def test_openssh_cmd(sshtarget):
    p = subprocess.run(
        sshtarget.openssh_cmd("whoami && printf '%s\\n' foo 1>&2 && exit 73"),
        capture_output=True,
        encoding="utf-8",
    )
    assert p.returncode == 73
    assert p.stdout == f"{sshtarget.user}\n"
    assert p.stderr == "foo\n"
    assert (
        Path.home()
        .joinpath(".ssh", f"yaesm-controlmaster-{sshtarget.user}@{sshtarget.host}:{sshtarget.port}")
        .exists()
    )

    p = subprocess.run(
        sshtarget.openssh_cmd(
            "(printf '%s\\n' foo && printf '%s\\n' bar && printf '%s\\n' baz"
            " && 1>&2 printf '%s\\n' quux) | grep ba; exit 42"
        ),
        capture_output=True,
        encoding="utf-8",
    )
    assert p.returncode == 42
    assert p.stdout == "bar\nbaz\n"
    assert p.stderr == "quux\n"

    p = subprocess.run(
        sshtarget.openssh_cmd(
            "printf '%s\\n%s\\n' 'hello from remote' 'bar' && 1>&2 printf '%s\\n' 'hello stderr'",
            string=True,
        )
        + " | grep hello; exit 12",
        shell=True,
        capture_output=True,
        encoding="utf-8",
    )
    assert p.returncode == 12
    assert p.stdout == "hello from remote\n"
    assert p.stderr == "hello stderr\n"


def test_with_path(sshtarget):
    new_sshtarget = sshtarget.with_path(Path("/foo"))
    assert new_sshtarget.path == Path("/foo")
    assert new_sshtarget.user == sshtarget.user
    assert new_sshtarget.host == sshtarget.host
    assert new_sshtarget.key == sshtarget.key


def test_can_connect(sshtarget, tmp_user):
    new_sshtarget = sshtarget.with_path(Path("/foo"))
    assert new_sshtarget.can_connect()
    new_sshtarget.user = tmp_user.pw_name
    assert not new_sshtarget.can_connect()


def test_exists(sshtarget, path_generator):
    path1 = path_generator("foo")
    path2 = path_generator("bar")
    target1 = sshtarget.with_path(path1)
    target2 = sshtarget.with_path(path2)
    assert not target1.exists()
    assert not target2.exists()
    path1.mkdir()
    path2.touch()
    assert target1.exists()
    assert target2.exists()
    assert target1.exists(path2)


def test_is_dir(sshtarget, path_generator):
    path1 = path_generator("foo")
    path2 = path_generator("bar")
    target1 = sshtarget.with_path(path1)
    target2 = sshtarget.with_path(path2)
    assert not target1.is_dir()
    assert not target2.is_dir()
    path1.mkdir()
    path2.touch()
    assert target1.is_dir()
    assert not target2.is_dir()
    assert target2.is_dir(path1)


def test_is_file(sshtarget, path_generator):
    path1 = path_generator("foo", cleanup=True)
    path2 = path_generator("bar", cleanup=True)
    target1 = sshtarget.with_path(path1)
    target2 = sshtarget.with_path(path2)
    assert not target1.is_file()
    assert not target2.is_file()
    path1.mkdir()
    path2.touch()
    assert not target1.is_file()
    assert target2.is_file()
    assert target1.is_file(path2)


def test_mkdir(sshtarget, path_generator):
    path = path_generator("foo", cleanup=True)
    assert sshtarget.mkdir(d=path)
    assert path.is_dir()
    path = path_generator("foo", cleanup=True)
    path = path.joinpath("bar").joinpath("baz")
    assert not sshtarget.mkdir(d=path, check=False)
    assert not path.is_dir()
    assert sshtarget.mkdir(d=path, parents=True)
    assert path.is_dir()
    path = path_generator("foo", cleanup=True)
    newtarget = sshtarget.with_path(path)
    assert not path.is_dir()
    newtarget.mkdir()
    assert path.is_dir()


def test_touch(sshtarget, path_generator):
    path = path_generator("foo", cleanup=True)
    assert sshtarget.touch(f=path)
    assert sshtarget.is_file(f=path)
    path = path_generator("foo", cleanup=True)
    newsshtarget = sshtarget.with_path(path)
    assert not path.is_file()
    assert newsshtarget.touch()
    assert path.is_file()


def test_mtime(sshtarget, path_generator):
    # file
    path = path_generator("foo", cleanup=True)
    path.touch()
    target = sshtarget.with_path(path)
    remote_mtime = target.mtime()
    local_mtime = path.stat().st_mtime
    assert abs(remote_mtime - local_mtime) < 1.0

    # directory
    d = path_generator("dir", cleanup=True, mkdir=True)
    target_d = sshtarget.with_path(d)
    remote_mtime_d = target_d.mtime()
    local_mtime_d = d.stat().st_mtime
    assert abs(remote_mtime_d - local_mtime_d) < 1.0

    # explicit path argument
    path2 = path_generator("bar", cleanup=True)
    path2.touch()
    remote_mtime2 = sshtarget.mtime(f=path2)
    local_mtime2 = path2.stat().st_mtime
    assert abs(remote_mtime2 - local_mtime2) < 1.0

    # nonexistent path raises
    bad_path = path_generator("nonexistent")
    with pytest.raises(subprocess.CalledProcessError):
        sshtarget.mtime(f=bad_path)
