### conftest.py is implicitly imported into all pytest test files. This file
### can be thought of as a collection of globally available pytest fixtures.

import pytest
import subprocess
import os
import pwd
import grp
import shutil
import random
from random import choice
from string import ascii_lowercase
from pathlib import Path

import yaesm.backup as bckp
from yaesm.sshtarget import SSHTarget
from yaesm.timeframe import Timeframe, FiveMinuteTimeframe, HourlyTimeframe, DailyTimeframe, WeeklyTimeframe, MonthlyTimeframe, YearlyTimeframe

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
    try:
        group = grp.getgrnam(group_name)
    except:
        subprocess.run(["groupadd", group_name], check=True)
        group = grp.getgrnam(group_name)
    return group

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
    def generator(name_prefix, base_dir="/tmp", suffix_length=5, mkdir=False, touch=False, cleanup=False):
        base_dir = Path(base_dir)
        tmp_path = None
        while tmp_path is None or tmp_path.exists():
            basename = name_prefix + random_string_generator(length=suffix_length)
            tmp_path = base_dir.joinpath(basename)
        if mkdir:
            tmp_path.mkdir(parents=True)
        elif touch:
            tmp_path.touch(exist_ok=False)
        if cleanup:
            tmp_paths_to_cleanup.append(tmp_path)
        return tmp_path

    yield generator

    for path in tmp_paths_to_cleanup:
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

@pytest.fixture
def random_backup_generator(random_timeframes_generator, sshtarget, path_generator, random_string_generator):
    """Fixture for generating random Backups."""
    names = []
    def generator(src_dir, dst_dir_base="/tmp", backup_type=None, num_timeframes=3):
        name = None
        dst_dir = path_generator(f"yaesm-test-backup-dst-dir-{name}-", base_dir=dst_dir_base, mkdir=True)
        while name is None or name in names:
            name = "test-backup-" + random_string_generator()
        names.append(name)
        backup_type = backup_type if backup_type is not None else random.choice(["local_to_local", "local_to_remote", "remote_to_local"])
        timeframes = random_timeframes_generator(num=num_timeframes)
        if backup_type == "local_to_local":
            return bckp.Backup(name, src_dir, dst_dir, timeframes)
        elif backup_type == "local_to_remote":
            return bckp.Backup(name, src_dir, sshtarget.with_path(dst_dir), timeframes)
        else: # remote_to_local
            return bckp.Backup(name, sshtarget.with_path(src_dir), dst_dir, timeframes)
    return generator

@pytest.fixture
def random_backups_generator(random_backup_generator):
    """Fixture for generating a list of random Backups. See 'random_backup_generator'
    for more information.
    """
    def generator(num=3, src_dir_base="/tmp", dst_dir_base="/tmp", num_timeframes=3):
        backups = []
        for _ in range(num):
            backups.append(random_backup_generator, src_dir_base=src_dir_base, dst_dir_base=dst_dir_base, num_timeframes=num_timeframes)
        return backups
    return generator

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

@pytest.fixture
def random_timeframe_generator(random_timeframe_times_generator, random_timeframe_minutes_generator, random_timeframe_weekdays_generator, random_timeframe_monthdays_generator, random_timeframe_yeardays_generator):
    """Fixture for generating random Timeframes."""
    def generator(tframe_type=None, keep=None, minutes=None, times=None, weekdays=None, monthdays=None, yeardays=None) -> Timeframe:
        tframe_type = random.choice(Timeframe.tframe_types()) if tframe_type is None else tframe_type
        keep        = random.randint(0,10) if keep is None else keep
        minutes     = random_timeframe_minutes_generator(random.randint(0,5)) if minutes is None else minutes
        times       = random_timeframe_times_generator(random.randint(0,5)) if times is None else times
        weekdays    = random_timeframe_weekdays_generator(random.randint(0,3)) if weekdays is None else weekdays
        monthdays   = random_timeframe_monthdays_generator(random.randint(0,3)) if monthdays is None else monthdays
        yeardays    = random_timeframe_yeardays_generator(random.randint(0,3)) if yeardays is None else yeardays
        if tframe_type == FiveMinuteTimeframe:
            return FiveMinuteTimeframe(keep)
        elif tframe_type == HourlyTimeframe:
            return HourlyTimeframe(keep, minutes)
        elif tframe_type == DailyTimeframe:
            return DailyTimeframe(keep, times)
        elif tframe_type == WeeklyTimeframe:
            return WeeklyTimeframe(keep, times, weekdays)
        elif tframe_type == MonthlyTimeframe:
            return MonthlyTimeframe(keep, times, monthdays)
        elif tframe_type == YearlyTimeframe:
            return YearlyTimeframe(keep, times, yeardays)
    return generator

@pytest.fixture
def random_timeframe(random_timeframe_generator):
    """Fixture for generating a single random timeframe. See the fixture
    'random_timeframe_generator' for more information.
    """
    return random_timeframe_generator()

@pytest.fixture
def random_timeframes_generator(random_timeframe_generator):
    """Fixture for generating a list of random Timeframes. See the fixture
    random_timeframe_generator for more details."""
    def generator(num=3, **kwargs):
        timeframes = []
        tframe_types = random.sample(Timeframe.tframe_types(), k=num)
        for tframe_type in tframe_types:
            timeframes.append(random_timeframe_generator(tframe_type=tframe_type, **kwargs))
        return timeframes
    return generator

@pytest.fixture
def random_timeframe_minutes_generator():
    """Fixture to generate a list of random minutes."""
    def generator(num=3):
        minutes = []
        for _ in range(num):
            minute = None
            while minute is None or minute in minutes:
                minute = random.randint(0,59)
            minutes.append(minute)
        return minutes
    return generator

@pytest.fixture
def random_timeframe_timespecs_generator():
    """Fixture to generate a list of random timeframe timespecs."""
    def generator(num=3):
        timespecs = []
        for _ in range(num):
            timespec = None
            while timespec is None or timespec in timespecs:
                hour = str(random.randint(0,23)).zfill(2)
                minute = str(random.randint(0,59)).zfill(2)
                timespec = f"{hour}:{minute}"
            timespecs.append(timespec)
        return timespecs
    return generator

@pytest.fixture
def random_timeframe_times_generator(random_timeframe_timespecs_generator):
    """Fixture to generate a list of random timeframe times."""
    def generator(num=3):
        timespecs = random_timeframe_timespecs_generator(num=num)
        times = list(map(lambda x: Timeframe.timespec_to_time(x), timespecs))
        return times
    return generator

@pytest.fixture
def random_timeframe_weekdays_generator():
    """Fixture to generate a list of random timeframe weekdays."""
    def generator(num=3):
        weekdays = random.sample(["monday","tuesday","wednesday","thursday","friday","saturday","sunday"], k=num)
        return weekdays
    return generator

@pytest.fixture
def random_timeframe_monthdays_generator():
    """Fixture to generate a list or random Timeframe monthdays."""
    def generator(num=3):
        monthdays = random.sample(list(range(1,32)), k=num)
        return monthdays
    return generator

@pytest.fixture
def random_timeframe_yeardays_generator():
    """Fixture to generate a list of random Timeframe yeardays."""
    def generator(num=3, include_leap=False):
        max_day = 366 if include_leap else 365
        yeardays = random.sample(list(range(1,max_day+1)), k=num)
        return yeardays
    return generator

@pytest.fixture(scope="module")
def btrfsbackend():
    """Fixture to provide a BtrfsBackend object."""
    btrfsbackend = BtrfsBackend()
    return btrfsbackend

@pytest.fixture
def btrfs_fs_generator(path_generator, loopback_generator):
    """Fixture to generate a btrfs filesystem on a loopback device."""
    def generator():
        mountpoint = path_generator("test-yaesm-btrfs-mountpoint", base_dir="/mnt", mkdir=True)
        loop = loopback_generator()
        subprocess.run(["mkfs", "-t", "btrfs", loop], check=True)
        subprocess.run(["mount", loop, mountpoint], check=True)
        subprocess.run(["btrfs", "subvolume", "create", f"{mountpoint}/@"], check=True)
        subprocess.run(["umount", mountpoint], check=True)
        subprocess.run(["mount", loop, "-o", "rw,noatime,subvol=@", mountpoint], check=True)
        return mountpoint
    return generator

@pytest.fixture
def btrfs_fs(btrfs_fs_generator):
    """Fixture to provide a single btrfs filesystem on a loopback device. See
    the 'btrfs_fs_generator' fixture for more details.
    """
    return btrfs_fs_generator()

@pytest.fixture
def btrfs_sudo_access(yaesm_test_users_group, tmp_path_factory):
    """Fixture to give users in the 'yaesm_test_users_group' group passwordless
    sudo access to the 'btrfs' executable. Users created with the 'tmp_user_generator'
    fixture are always assigned membership to this group.
    """
    btrfs = shutil.which("btrfs")
    sudoers_rules = [
        f"%{yaesm_test_users_group.gr_name} ALL = NOPASSWD: {btrfs} subvolume snapshot -r *",
        f"%{yaesm_test_users_group.gr_name} ALL = NOPASSWD: {btrfs} subvolume delete *",
        f"%{yaesm_test_users_group.gr_name} ALL = NOPASSWD: {btrfs} send *",
        f"%{yaesm_test_users_group.gr_name} ALL = NOPASSWD: {btrfs} receive *"
    ]
    sudo_rule_file = Path("/etc/sudoers.d/yaesm-test-btrfs-sudo-rule")
    if not sudo_rule_file.is_file():
        with open(sudo_rule_file, "w") as f:
            for rule in sudoers_rules:
                f.write(rule + "\n")
    return True

@pytest.fixture
def example_valid_config_spec():
    """See example config "test_config.yaml" for notes."""
    return {
        "root_backup": {
            "backend": "rsync",
            "src_dir": "/",
            "dst_dir": "/mnt/backupdrive/yaesm/root_backup",
            "exclude": ["~/.cache"],
            "exclude_from": "somefile.txt",
            "timeframes": ["5minute", "hourly", "daily", "weekly", "monthly", "yearly"],
            "5minute_keep": 24,
            "hourly_minutes": [0, 30],
            "hourly_keep": 48,
            "daily_times": ["23:59", "11:59"],
            "daily_keep": 720,
            "weekly_keep": 21,
            "weekly_days": ["monday", "friday", "sunday"],
            "weekly_times": ["23:59", "05:30"],
            "monthly_keep": 100,
            "monthly_days": [1, 7, 30],
            "monthly_times": ["23:59"],
            "yearly_keep": 5,
            "yearly_days": [1, 129],
            "yearly_times": ["23:59"]},
        "home_backup": {
            "backend": "btrfs",
            "src_dir": "/home/fred",
            "dst_dir": "fred@192.168.1.73:/yaesm/fred_laptop_backups",
            "ssh_key": "/home/fred/.ssh/id_rsa",
            "ssh_config": "/etc/ssh/some_other_config",
            "timeframes": ["daily"],
            "daily_times": ["23:59"],
            "daily_keep": 365},
        "database_snapshot": {
            "backend": "zfs",
            "src_dataset": "/important-database",
            "dst_dataset": "/.snapshots/yaesm/important-database",
            "timeframes": ["hourly"],
            "hourly_keep": 10000000000,
            "hourly_minutes": [0]}}
