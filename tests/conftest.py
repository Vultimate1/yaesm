import pytest
import subprocess
import os
import pwd
import grp
from random import choice
from string import ascii_lowercase
from pathlib import Path

@pytest.fixture
def superuser():
    """Fixture to skip the test if we are not the superuser."""
    if os.geteuid() != 0:
        pytest.skip("superuser required")
    return True

@pytest.fixture
def tmp_base_0755_perms(tmp_path_factory):
    """Fixture to ensure the base of temporary directories have 0755 permissions.
    By default these directories have 0700 permissions meaning only the owner of
    the temp base can access its contents. Puts permissions back to original
    during cleanup.
    """
    tmp_base = tmp_path_factory.getbasetemp()
    tmp_base_mode = tmp_base.stat().st_mode
    tmp_base_base = tmp_base.parent
    tmp_base_base_mode = tmp_base_base.stat().st_mode
    tmp_base.chmod(0o755)
    tmp_base_base.chmod(0o755)
    yield
    tmp_base.chmod(tmp_base_mode)
    tmp_base_base.chmod(tmp_base_base_mode)

@pytest.fixture
def loopback_generator(superuser, tmp_path_factory):
    """Fixture to generate and cleanup loopback devices."""
    loopbacks = []
    def generator():
        loopfile = tmp_path_factory.mktemp("loop") / "loop"
        subprocess.run(["truncate", "--size", "1G", loopfile], check=True)
        losetup = subprocess.run(["losetup", "--find", "--show", loopfile], check=True, capture_output=True, encoding="utf-8")
        loop = Path(losetup.stdout.removesuffix("\n"))
        loopbacks.append(loop)
        return loop

    yield generator

    for loop in loopbacks:
        subprocess.run(["losetup", "--detach", loop], check=True)

@pytest.fixture
def loopback(loopback_generator):
    """Fixture for a single loopback device."""
    return loopback_generator()

@pytest.fixture
def localhost_server_generator(ssh_key_generator, tmp_user_generator, tmp_path_factory):
    """Fixture to setup mock ssh servers on the localhost. This fixture makes
    key auth possible as it will create and add the ssh_key to the localhost
    server users ~/.ssh/authorized_keys file to allow passwordless login.
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
    """Fixture to setup a single localhost mock ssh server."""
    return localhost_server_generator()

@pytest.fixture
def ssh_key_generator(tmp_path_factory):
    """Fixture for creating temporary ssh keys. Returns path to the generated
    private ssh key. The public key name is the private key name suffixed with
    '.pub'.
    """
    def generator():
        key = tmp_path_factory.mktemp("tmp_ssh_key").joinpath("id_rsa")
        subprocess.run(["ssh-keygen", "-N", "", "-t", "rsa", "-b", "4096", "-f", key], check=True)
        return key
    return generator # cleanup happens automatically due to tmp_path_factory

@pytest.fixture
def yaesm_test_users_group(superuser):
    """Fixture for creating a temporary group named 'yaesm-test-users'. Returns
    a grp object for the created group.
    """
    group = "yaesm-test-users"
    subprocess.run(["groupadd", "--force", group], check=True)
    yield grp.getgrnam(group)
    subprocess.run(["groupdel", group], check=True)

@pytest.fixture
def tmp_user_home(tmp_base_0755_perms, tmp_path_factory):
    """Fixture to provide the temporary directory to be used as the base home
    directory for temporary users. Note that this uses the them 'tmp_base_0755_perms'
    fixture to ensure that the temporary directory base can actually be reached
    by the temporary user.
    """
    home = tmp_path_factory.mktemp("tmp_user_home")
    home.chmod(0o755)
    return home

@pytest.fixture
def tmp_user_generator(superuser, yaesm_test_users_group, tmp_user_home):
    """Fixture to generate and cleanup temporary users on the system. Note that
    the created user has a locked password meaning only root can sign in as this
    user. The returned value is a pwd object for the created user.
    """
    tmp_users = []
    def generator():
        username = ""
        while True:
            try:
                username = "yaesm-test-user-" + ''.join(choice(ascii_lowercase) for i in range(5))
                pwd.getpwnam(username)
            except:
                break
        subprocess.run(["useradd", "-m", "-b", tmp_user_home, "-G", yaesm_test_users_group.gr_name, username], check=True)
        subprocess.run(["passwd", "--lock", username], check=True)
        user = pwd.getpwnam(username)
        tmp_users.append(user)
        return user

    yield generator

    for user in tmp_users:
        # users home dir is a tmp_path so no need to use --remove option here
        subprocess.run(["userdel", "--force", user.pw_name], check=True)

@pytest.fixture
def tmp_user(tmp_user_generator):
    """Fixture for creating and deleting a temporary user on the system. Note
    that the returned user is a pwd object.
    """
    return tmp_user_generator()
