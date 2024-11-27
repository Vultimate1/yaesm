import pytest
import yaesm.backup as bckp
from yaesm.sshtarget import SSHTarget
from test_sshtarget import sshtarget, sshtarget_generator

# TODO: Need to be able to generate Timeframes first
# @pytest.fixture
# def backup_generator():
#    ...

def test_backup_to_datetime():
    dt = bckp.backup_to_datetime("yaesm-backup@1999_05_13_10:30")
    dt.year == 1999
    dt.month == 5
    dt.day == 13
    dt.hour == 10
    dt.minute == 30

    # also should accept full paths
    dt = bckp.backup_to_datetime("/some/path/yaesm-backup@1999_05_13_10:30")
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
    assert bckp.backups_collect(sshtarget) == list(map(lambda bn: target.joinpath(bn), backup_basenames))
