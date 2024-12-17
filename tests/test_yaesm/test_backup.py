import pytest
from freezegun import freeze_time

import yaesm.backup as bckp
from yaesm.sshtarget import SSHTarget

def test_backup_to_datetime(sshtarget):
    # accept full paths
    dt = bckp.backup_to_datetime("/some/path/yaesm-backup@1999_05_13_10:30")
    dt.year == 1999
    dt.month == 5
    dt.day == 13
    dt.hour == 10
    dt.minute == 30

    # accept just basename
    dt = bckp.backup_to_datetime("yaesm-backup@1999_05_13_10:30")
    dt.year == 1999
    dt.month == 5
    dt.day == 13
    dt.hour == 10
    dt.minute == 30

    # accept SSHTarget
    dt = bckp.backup_to_datetime(sshtarget.with_path("/some/path/yaesm-backup@1999_05_13_10:30"))
    dt.year == 1999
    dt.month == 5
    dt.day == 13
    dt.hour == 10
    dt.minute == 30

def test_backups_sorted():
    backups_sorted = bckp.backups_sorted([
        "yaesm-backup@1999_05_13_10:30",
        "/path/to/backup/yaesm-backup@1999_05_13_11:30",
        "/path/to/backup/yaesm-backup@1999_05_13_09:30",
        "yaesm-backup@1999_05_13_08:30",
        "yaesm-backup@1999_05_13_12:30",
        "yaesm-backup@1999_05_13_13:30",
        "yaesm-backup@1999_05_13_10:30"
    ])
    assert backups_sorted == [
        "yaesm-backup@1999_05_13_13:30",
        "yaesm-backup@1999_05_13_12:30",
        "/path/to/backup/yaesm-backup@1999_05_13_11:30",
        "yaesm-backup@1999_05_13_10:30",
        "yaesm-backup@1999_05_13_10:30",
        "/path/to/backup/yaesm-backup@1999_05_13_09:30",
        "yaesm-backup@1999_05_13_08:30"
    ]

def test_backup_basename_re():
    backup_basename_re = bckp.backup_basename_re()
    assert backup_basename_re.match("yaesm-foo-backup@1999_05_13_23:59")
    assert backup_basename_re.match("yaesm-foobackup@1999_05_13_23:59")
    assert not backup_basename_re.match("foo-backup@1999_05_13_23:59")
    assert not backup_basename_re.match("yaesm-foo-backup@1999_05_13_23:5")
    re_result = backup_basename_re.match("yaesm-foo-backup@1999_05_13_23:59")
    assert re_result.group(1) == "yaesm-foo-backup"
    assert re_result.group(2) == "1999_05_13_23:59"

def test_backup_basename_now(random_backup_generator, random_timeframe):
    random_backup = random_backup_generator("/tmp")
    with freeze_time("1999-05-13 23:59"):
        assert bckp.backup_basename_now(random_backup, random_timeframe) == f"yaesm-{random_backup.name}-{random_timeframe.name}@1999_05_13_23:59"

def test_backup_basename_update_time(random_backup_generator, random_timeframe):
    random_backup = random_backup_generator("/tmp")
    backup_basename = ""
    with freeze_time("1999-05-13 23:59"):
        backup_basename = bckp.backup_basename_now(random_backup, random_timeframe)
        assert backup_basename == f"yaesm-{random_backup.name}-{random_timeframe.name}@1999_05_13_23:59"
        with freeze_time("1999-12-25 23:59"):
            backup_basename = bckp.backup_basename_update_time(backup_basename)
            assert backup_basename == f"yaesm-{random_backup.name}-{random_timeframe.name}@1999_12_25_23:59"

def test_backups_collect(path_generator, sshtarget):
    backup_basenames = [
        "yaesm-backup@1999_05_13_13:30",
        "yaesm-backup@1999_05_13_12:30",
        "yaesm-backup@1999_05_13_11:30",
        "yaesm-backup@1999_05_13_10:30",
        "yaesm-backup@1999_05_13_09:30",
        "yaesm-backup@1999_05_13_08:30"
    ]

    ### Test collection of a local target dir
    target = path_generator("yaesm-test-backups-collect-local")
    for bn in backup_basenames:
        target.joinpath(bn).mkdir(parents=True, exist_ok=True)
    assert bckp.backups_collect(target) == list(map(lambda bn: target.joinpath(bn), backup_basenames))


    ### Test collection from an SSHTarget (remember that sshtarget is on the localhost)
    target = path_generator("yaesm-test-backups-collect-sshtarget")
    for bn in backup_basenames:
        target.joinpath(bn).mkdir(parents=True, exist_ok=True)
    sshtarget.path = target
    got = bckp.backups_collect(sshtarget)
    assert list(map(lambda sshtarget: sshtarget.path, got)) == list(map(lambda bn: target.joinpath(bn), backup_basenames))
