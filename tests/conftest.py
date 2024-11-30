### conftest.py is implicitly imported into all pytest test files. This file
### can be thought of as a collection of globally available pytest fixtures.

import pytest
import subprocess
import os
import pwd
import grp
import shutil
from random import choice
from string import ascii_lowercase
from pathlib import Path

@pytest.fixture
def loopback_generator(path_generator):
    """Fixture to generate loopback devices."""
    loops = []
    def generator():
        loopfile = None
        while loopfile is None or loopfile.is_file():
            loopfile = path_generator("yaesm-test-loopfile")
        subprocess.run(["truncate", "--size", "1G", loopfile], check=True)
        losetup = subprocess.run(["losetup", "--find", "--show", loopfile], check=True, capture_output=True, encoding="utf-8")
        loop = Path(losetup.stdout.removesuffix("\n"))
        loops.append(loop)
        return loop

    yield generator

    for loop in loops:
        subprocess.run(["losetup", "--detach", loop], check=True)

@pytest.fixture
def loopback(loopback_generator):
    """Fixture to generate a single loopback device. See the loopback_generator
    fixture for more information.
    """
    return loopback_generator()

@pytest.fixture
def localhost_server_generator(ssh_key_generator, tmp_user_generator):
    """Fixture to setup mock ssh servers on the localhost. This fixture makes
    key auth possible as it will create and add the ssh_key to the localhost
    server users ~/.ssh/authorized_keys file to allow passwordless login.
    The generator function returns a hash - {"user":pwd_object, "key":path_to_key}
    """
    def generator():
        user = tmp_user_generator()
        os.makedirs(f"{user.pw_dir}/.ssh", mode=0o700)
        os.chown(f"{user.pw_dir}/.ssh", user.pw_uid, user.pw_gid)
        authorized_keys = f"{user.pw_dir}/.ssh/authorized_keys"
        privkey = ssh_key_generator()
        pubkey = privkey.with_suffix(".pub")
        with open(pubkey, "r") as fr, open(authorized_keys, "a") as fw:
            for l in fr:
                fw.write(l)
        os.chmod(authorized_keys, 0o600)
        os.chown(authorized_keys, user.pw_uid, user.pw_gid)
        return {"user":user, "key":privkey}
    return generator

@pytest.fixture
def localhost_server(localhost_server_generator):
    """Fixture to setup a single mock ssh server on the localhost. See
    the localhost_server_generator fixture for more information."""
    return localhost_server_generator()

@pytest.fixture
def ssh_key_generator(path_generator):
    """Fixture for generating ssh keys. Returns path to the generated private
    ssh key. The public key name is the private key name suffixed with '.pub'.
    """
    def generator():
        key = path_generator("id_rsa")
        subprocess.run(["ssh-keygen", "-N", "", "-t", "rsa", "-b", "4096", "-f", key], check=True)
        return key
    return generator

@pytest.fixture
def ssh_key(ssh_key_generator):
    """Fixture to provide a single ssh key. See the ssh_key_generator fixture
    for more information.
    """
    return ssh_key_generator()

@pytest.fixture
def yaesm_test_users_group():
    """Fixture for creating a group named 'yaesm-test-users'. Returns a grp
    object for the created group.
    """
    group_name = "yaesm-test-users"
    subprocess.run(["groupadd", "--force", group_name], check=True)
    return grp.getgrnam(group_name)

@pytest.fixture
def tmp_user_generator(yaesm_test_users_group, random_string_generator):
    """Fixture to generate and cleanup temporary users on the system. Note that
    the created user has a locked password meaning only root can sign in as this
    user. The returned value is a pwd object for the created user.
    """
    def generator():
        username = None
        while True:
            try:
                username = "yaesm-test-user-" + random_string_generator()
                pwd.getpwnam(username)
            except:
                break
        subprocess.run(["useradd", "-m", "-G", yaesm_test_users_group.gr_name, username], check=True)
        subprocess.run(["passwd", "--lock", username], check=True)
        user = pwd.getpwnam(username)
        return user
    return generator

@pytest.fixture
def tmp_user(tmp_user_generator):
    """Fixture for creating a temporary user on the system. See the tmp_user_generator
    fixture for more information.
    """
    return tmp_user_generator()

@pytest.fixture
def random_string_generator():
    """Fixture for generating random ascii lowercase strings of arbitrary length."""
    def generator(length=5):
        return "".join(choice(ascii_lowercase) for i in range(length))
    return generator

@pytest.fixture
def path_generator(random_string_generator):
    """Fixture for generating paths that do not exist on the system. Allows
    callers to specify the prefix of the basename of the path, the length of the
    basenames random suffix, the base_dir of the path, and if the path should be
    removed during cleanup.
    """
    tmp_paths_to_cleanup = []
    def generator(name_prefix, base_dir="/tmp", suffix_length=5, mkdir=False, cleanup=False):
        base_dir = Path(base_dir)
        tmp_path = None
        while tmp_path is None or tmp_path.exists():
            basename = name_prefix + random_string_generator(length=suffix_length)
            tmp_path = base_dir.joinpath(basename)
        if mkdir:
            tmp_path.mkdir(parents=True)
        if cleanup:
            tmp_paths_to_cleanup.append(tmp_path)
        return tmp_path

    yield generator

    for path in tmp_paths_to_cleanup:
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
