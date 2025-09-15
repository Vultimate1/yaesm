"""tests/test_yaesm/test_config.py"""
# import pytest

from yaesm.config import parse_file
from yaesm.sshtarget import SSHTarget
from yaesm.timeframe import FiveMinuteTimeframe, HourlyTimeframe, DailyTimeframe, \
    WeeklyTimeframe, MonthlyTimeframe, YearlyTimeframe

def test_parse_file():
    """Tests on a valid YAML config file."""
    backups = parse_file("tests/test_config.yaml")
    assert isinstance(backups, list)
    backup_names = [backup.name for backup in backups]
    backup_srcs = [backup.src_dir for backup in backups]
    backup_dsts = [backup.dst_dir for backup in backups]
    assert backup_names == ["root_backup", "home_backup", "database_snapshot"]
    assert backup_srcs == ["/", "/home/fred", "/important-database"]
    assert backup_dsts == ["/mnt/backupdrive/yaesm/root_backup",
                           "fred@192.168.1.73:/yaesm/fred_laptop_backups",
                           "/.snapshots/yaesm/important-database"]
    # TODO assert exclude & exclude_from once added.
    root_tfs, home_tfs, data_tfs = [backup.timeframes for backup in backups]
    root_tfs_keep = [obj.keep for obj in root_tfs]
    tfs = [FiveMinuteTimeframe, HourlyTimeframe, DailyTimeframe, WeeklyTimeframe,
           MonthlyTimeframe, YearlyTimeframe]
    for tf in tfs:
        assert tf in [type(obj) for obj in root_tfs]
    assert root_tfs_keep == [24, 48, 720, 21, 100, 5]
    assert root_tfs
    assert isinstance(home_tfs[0], DailyTimeframe)
    assert home_tfs[0].keep == 365
    assert home_tfs[0].times == ["23:59"]
    assert isinstance(data_tfs[0], HourlyTimeframe)
    assert data_tfs[0].keep == 10000000000
    assert data_tfs[0].minutes == [0]
