"""tests/test_yaesm/test_config.py"""
import pytest
import shutil
import voluptuous as vlp
from pathlib import Path
import re

import yaesm.config as config
from yaesm.sshtarget import SSHTarget
from yaesm.timeframe import FiveMinuteTimeframe, HourlyTimeframe, DailyTimeframe, \
    WeeklyTimeframe, MonthlyTimeframe, YearlyTimeframe

def test_Schema_schema_empty():
    schema = config.Schema.schema_empty()
    assert schema("") == ""
    assert schema("foo") == "foo"
    assert schema(12) == 12
    assert schema({"foo": "bar", "baz": 12}) == {"foo": "bar", "baz": 12}

def test_Schema_is_file(path_generator):
    tmpfile_str = str(path_generator("tmpfile", touch=True))
    assert config.Schema.is_file(tmpfile_str) == Path(tmpfile_str)
    assert config.Schema.is_file(Path(tmpfile_str)) == Path(tmpfile_str)

    Path(tmpfile_str).unlink()
    with pytest.raises(vlp.Invalid) as exc:
        config.SrcDirDstDirSchema.is_file(tmpfile_str)
    assert str(exc.value) == config.Schema.ErrMsg.LOCAL_FILE_INVALID

    with pytest.raises(vlp.Invalid) as exc:
        tmpfile_relative = path_generator("tmpfile", base_dir=".", touch=True)
        tmpfile_relative_str = "./" + tmpfile_relative.name
        assert Path(tmpfile_relative_str).is_file()
        config.SrcDirDstDirSchema.is_file(tmpfile_relative_str)
    assert str(exc.value) == config.Schema.ErrMsg.LOCAL_FILE_INVALID

def test_Schems_is_dir(path_generator):
    tmpdir_str = str(path_generator("tmpdir", mkdir=True))
    assert config.SrcDirDstDirSchema.is_dir(tmpdir_str) == Path(tmpdir_str)
    assert config.SrcDirDstDirSchema.is_dir(Path(tmpdir_str)) == Path(tmpdir_str)

    shutil.rmtree(tmpdir_str)
    with pytest.raises(vlp.Invalid) as exc:
        config.SrcDirDstDirSchema.is_dir(tmpdir_str)
    assert str(exc.value) == config.Schema.ErrMsg.LOCAL_DIR_INVALID

    with pytest.raises(vlp.Invalid) as exc:
        tmpdir_relative = path_generator("tmpdir", base_dir=".", mkdir=True)
        tmpdir_relative_str = "./" + tmpdir_relative.name
        assert Path(tmpdir_relative_str).is_dir()
        config.SrcDirDstDirSchema.is_dir(tmpdir_relative_str)
    assert str(exc.value) == config.Schema.ErrMsg.LOCAL_DIR_INVALID

def test_SrcDirDstDirSchema_is_sshtarget_spec():
    sshtarget_spec = "ssh://p22:root@localhost:/"
    assert config.SrcDirDstDirSchema._is_sshtarget_spec(sshtarget_spec) == sshtarget_spec

    with pytest.raises(vlp.Invalid) as exc:
        config.SrcDirDstDirSchema._is_sshtarget_spec("/")
    assert str(exc.value) == config.SrcDirDstDirSchema.ErrMsg.SSH_TARGET_SPEC_INVALID

    with pytest.raises(vlp.Invalid) as exc:
        config.SrcDirDstDirSchema._is_sshtarget_spec("")
    assert str(exc.value) == config.SrcDirDstDirSchema.ErrMsg.SSH_TARGET_SPEC_INVALID

def test_SrcDirDstDirSchema_is_dir_or_sshtarget_spec(path_generator):
    sshtarget_spec = "ssh://p22:root@localhost:/"
    assert config.SrcDirDstDirSchema._is_dir_or_sshtarget_spec(sshtarget_spec) == sshtarget_spec

    tmpdir_str = str(path_generator("tmpdir", mkdir=True))
    assert config.SrcDirDstDirSchema._is_dir_or_sshtarget_spec(tmpdir_str) == Path(tmpdir_str)
    assert config.SrcDirDstDirSchema._is_dir_or_sshtarget_spec(Path(tmpdir_str)) == Path(tmpdir_str)

    shutil.rmtree(tmpdir_str)
    with pytest.raises(vlp.Invalid) as exc:
        config.SrcDirDstDirSchema._is_dir_or_sshtarget_spec(tmpdir_str)
    assert str(exc.value) == config.SrcDirDstDirSchema.ErrMsg.NOT_VALID_SSHTARGET_SPEC_AND_NOT_VALID_LOCAL_DIR

def test_SrcDirDstDirSchema_dict_max_one_sshtarget_spec(path_generator):
    src_dir_str = str(path_generator("src_dir", mkdir=True))
    dst_dir_str = str(path_generator("dst_dir", mkdir=True))
    data = {"src_dir": src_dir_str, "dst_dir": dst_dir_str}
    assert config.SrcDirDstDirSchema._dict_max_one_sshtarget_spec(data) == data

    sshtarget_spec = "ssh://p22:root@localhost:/"

    data = {"src_dir": sshtarget_spec, "dst_dir": dst_dir_str}
    assert config.SrcDirDstDirSchema._dict_max_one_sshtarget_spec(data) == data

    data = {"src_dir": src_dir_str, "dst_dir": sshtarget_spec}
    assert config.SrcDirDstDirSchema._dict_max_one_sshtarget_spec(data) == data

    with pytest.raises(vlp.Invalid) as exc:
        sshtarget_spec2 = "ssh://p22:root@localhost:/foo"
        data = {"src_dir": sshtarget_spec, "dst_dir": sshtarget_spec2}
        config.SrcDirDstDirSchema._dict_max_one_sshtarget_spec(data)
    assert str(exc.value) == config.SrcDirDstDirSchema.ErrMsg.MULTIPLE_SSH_TARGET_SPECS

def test_SrcDirDstDirSchema_dict_ssh_key_required_if_ssh_target(path_generator):
    src_dir_str = str(path_generator("src_dir", mkdir=True))
    dst_dir_str = str(path_generator("dst_dir", mkdir=True))

    data = {"src_dir": src_dir_str, "dst_dir": dst_dir_str}
    assert config.SrcDirDstDirSchema._dict_ssh_key_required_if_ssh_target(data) == data

    sshtarget_spec = "ssh://p22:root@localhost:/"
    ssh_key_str = str(path_generator("ssh_key", touch=True))
    data = {"src_dir": sshtarget_spec, "dst_dir": dst_dir_str, "ssh_key": ssh_key_str}
    assert config.SrcDirDstDirSchema._dict_ssh_key_required_if_ssh_target(data) == {**data, "ssh_key": Path(data["ssh_key"])}

    with pytest.raises(vlp.Invalid) as exc:
        data = {"src_dir": sshtarget_spec, "dst_dir": dst_dir_str}
        config.SrcDirDstDirSchema._dict_ssh_key_required_if_ssh_target(data)
    assert str(exc.value) == config.SrcDirDstDirSchema.ErrMsg.SSH_KEY_MISSING

    Path(ssh_key_str).unlink()
    with pytest.raises(vlp.Invalid) as exc:
        data = {"src_dir": sshtarget_spec, "dst_dir": dst_dir_str, "ssh_key": ssh_key_str}
        config.SrcDirDstDirSchema._dict_ssh_key_required_if_ssh_target(data)
    assert str(exc.value) == config.Schema.ErrMsg.LOCAL_FILE_INVALID

def test_SrcDirDstDirSchema_dict_ssh_target_connectable(sshtarget_generator, path_generator):
    sshtarget = sshtarget_generator()
    data = {"src_dir": "/foo", "dst_dir": sshtarget.spec, "ssh_key": sshtarget.key}
    data = config.SrcDirDstDirSchema._dict_ssh_target_connectable(data)
    assert isinstance(data["dst_dir"], SSHTarget)

    ssh_config_str = str(path_generator("ssh_config", touch=True))
    data = {"src_dir": "/foo", "dst_dir": sshtarget.spec, "ssh_key": sshtarget.key, "ssh_config": ssh_config_str}
    data = config.SrcDirDstDirSchema._dict_ssh_target_connectable(data)
    assert data["ssh_config"] == Path(ssh_config_str)

    sshtarget = sshtarget_generator()
    with pytest.raises(vlp.Invalid) as exc:
        bad_key = path_generator("bad_key", touch=True)
        data = {"ssh_key": bad_key, "src_dir": "/foo", "dst_dir": sshtarget.spec}
        config.SrcDirDstDirSchema._dict_ssh_target_connectable(data)
    assert str(exc.value) == config.SrcDirDstDirSchema.ErrMsg.SSH_CONNECTION_FAILED_TO_ESTABLISH

    with pytest.raises(vlp.Invalid) as exc:
        bad_dir = path_generator("bad_dir", mkdir=False)
        sshtarget.spec = re.sub(r':(/[^:]+)$', f":{bad_dir}", sshtarget.spec)
        data = {"src_dir": sshtarget.spec, "dst_dir": "/foo", "ssh_key": sshtarget.key}
        config.SrcDirDstDirSchema._dict_ssh_target_connectable(data)
    assert str(exc.value) == config.SrcDirDstDirSchema.ErrMsg.REMOTE_DIR_INVALID

# def test_parse_file():
#     """Tests on a valid YAML config file."""
#     backups = config.parse_file("tests/test_config.yaml")
#     assert isinstance(backups, list)
#     backup_names = [backup.name for backup in backups]
#     backup_srcs = [backup.src_dir for backup in backups]
#     backup_dsts = [backup.dst_dir for backup in backups]
#     assert backup_names == ["root_backup", "home_backup", "database_snapshot"]
#     assert backup_srcs == ["/", "/home/fred", "/important-database"]
#     assert backup_dsts == ["/mnt/backupdrive/yaesm/root_backup",
#                            "fred@192.168.1.73:/yaesm/fred_laptop_backups",
#                            "/.snapshots/yaesm/important-database"]
#     # TODO assert exclude & exclude_from once added.
#     root_tfs, home_tfs, data_tfs = [backup.timeframes for backup in backups]
#     root_tfs_keep = [obj.keep for obj in root_tfs]
#     tfs = [FiveMinuteTimeframe, HourlyTimeframe, DailyTimeframe, WeeklyTimeframe,
#            MonthlyTimeframe, YearlyTimeframe]
#     for tf in tfs:
#         assert tf in [type(obj) for obj in root_tfs]
#     assert root_tfs_keep == [24, 48, 720, 21, 100, 5]
#     assert root_tfs
#     assert isinstance(home_tfs[0], DailyTimeframe)
#     assert home_tfs[0].keep == 365
#     assert home_tfs[0].times == ["23:59"]
#     assert isinstance(data_tfs[0], HourlyTimeframe)
#     assert data_tfs[0].keep == 10000000000
#     assert data_tfs[0].minutes == [0]
