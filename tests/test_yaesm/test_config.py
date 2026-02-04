"""tests/test_yaesm/test_config.py"""
import copy

import pytest
import shutil
import voluptuous as vlp
from pathlib import Path
import yaml
import re
import random

import yaesm.config as config
import yaesm.backup as bckp
from yaesm.backend.backendbase import BackendBase
from yaesm.sshtarget import SSHTarget
from yaesm.timeframe import Timeframe, FiveMinuteTimeframe, HourlyTimeframe, DailyTimeframe, WeeklyTimeframe, MonthlyTimeframe, YearlyTimeframe

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

def test_Schema_is_dir(path_generator):
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

def test_BackendSchema_schema():
    schema = config.BackendSchema.schema()

    data = {"backend": "btrfs"}
    data = schema(data)
    assert isinstance(data["backend"], BackendBase)  # Changed from issubclass
    assert len(data) == 1

    with pytest.raises(vlp.Invalid) as exc:
        data = {"backend": "THISISNOTAVALIDBACKENDNAME"}
        schema(data)
    assert re.match("^" + config.BackendSchema.ErrMsg.INVALID_BACKEND_NAME, str(exc.value))

    data = {"backend": "btrfs", "INVALID_KEY": "FOO"}
    schema(data)

    with pytest.raises(vlp.Invalid) as exc:
        data = {}
        schema(data)
    assert re.match("required key not provided @", str(exc.value))

def test_BackendSchema_dict_promote_backend_name_to_backend_instance():
    data = {"backend": "btrfs"}
    data = config.BackendSchema._dict_promote_backend_name_to_backend_instance(data)
    assert isinstance(data["backend"], BackendBase)  # Changed from issubclass
    assert len(data) == 1
    with pytest.raises(KeyError):
        data = {"FOO": "BAR", "BAZ": "QUUX"}
        config.BackendSchema._dict_promote_backend_name_to_backend_instance(data)

def test_TimeframeSchema_has_required_settings():
    data = {"timeframes": ["5minute", "hourly", "daily", "weekly", "monthly", "yearly"]}
    setting_keys = ["5minute_keep", "hourly_keep", "hourly_minutes", "daily_keep", "daily_times",
                    "weekly_keep", "weekly_times", "weekly_days", "monthly_keep", "monthly_times",
                    "monthly_days", "yearly_keep", "yearly_times", "yearly_days"]
    # The value is irrelevant for this method
    data.update(dict.fromkeys(setting_keys))
    assert config.TimeframeSchema.has_required_settings(data) == data

    data.pop("hourly_keep")
    with pytest.raises(vlp.Invalid) as exc:
        config.TimeframeSchema.has_required_settings(data)
    assert str(exc.value) == config.TimeframeSchema.ErrMsg.SETTING_MISSING \
        + "\n\thourly: ['hourly_keep']"

    # For the time being, this will only return the first error
    data.pop("weekly_times")
    with pytest.raises(vlp.Invalid) as exc:
        config.TimeframeSchema.has_required_settings(data)
    assert str(exc.value) == config.TimeframeSchema.ErrMsg.SETTING_MISSING \
        + "\n\thourly: ['hourly_keep']"

def test_TimeframeSchema_are_valid_timespecs():
    valid_specs = ["12:34", "23:59", "00:00", "99:99"]
    valid_expected = [[12, 34], [23, 59], [0, 0], [99, 99]]
    assert config.TimeframeSchema.are_valid_timespecs(valid_specs) == valid_expected

    invalid_specs = ["1:23", "12:3", "ab:cd", "1234", ""]
    for spec in invalid_specs:
        with pytest.raises(vlp.Invalid) as exc:
            config.TimeframeSchema.are_valid_timespecs([spec])
        assert str(exc.value) == config.TimeframeSchema.ErrMsg.TIME_MALFORMED \
            + f"\n\tExpected format 'hh:mm', got {spec}"

def test_TimeframeSchema_are_valid_hours():
    valid_specs = [[12, 34], [23, 59], [0, 0], [3, -1]]
    assert config.TimeframeSchema.are_valid_hours(valid_specs) == valid_specs

    invalid_specs = [[-1, 30], [24, 30]]
    for spec in invalid_specs:
        with pytest.raises(vlp.Invalid) as exc:
            config.TimeframeSchema.are_valid_hours([spec])
        assert str(exc.value) == config.TimeframeSchema.ErrMsg.HOUR_OUT_OF_RANGE \
            + f"\n\tGot {spec}"

def test_TimeframeSchema_are_valid_minutes():
    valid_specs = [[12, 34], [23, 59], [0, 0], [-1, 30]]
    assert config.TimeframeSchema.are_valid_minutes(valid_specs) == valid_specs

    invalid_specs = [[3, -1], [3, 60]]
    for spec in invalid_specs:
        with pytest.raises(vlp.Invalid) as exc:
            config.TimeframeSchema.are_valid_minutes([spec])
        assert str(exc.value) == config.TimeframeSchema.ErrMsg.MINUTE_OUT_OF_RANGE \
            + f"\n\tGot {spec}"

def test_TimeframeSchema_promote_timeframes_spec_to_list_of_timeframes(valid_raw_config):
    for backup_name in sorted(valid_raw_config.keys()):
        backup_settings = valid_raw_config[backup_name]
        orig_timeframes = backup_settings["timeframes"]
        backup_spec = config.TimeframeSchema._promote_timeframes_spec_to_list_of_timeframes(
            valid_raw_config[backup_name])
        assert len(orig_timeframes) == len(backup_spec["timeframes"])
        for timeframe in backup_spec["timeframes"]:
            assert isinstance(timeframe, Timeframe)
            if isinstance(timeframe, FiveMinuteTimeframe):
                assert timeframe.name == "5minute"
                assert timeframe.keep == backup_spec["5minute_keep"]
            elif isinstance(timeframe, HourlyTimeframe):
                assert timeframe.name == "hourly"
                assert timeframe.keep == backup_spec["hourly_keep"]
                assert timeframe.minutes == backup_spec["hourly_minutes"]
            elif isinstance(timeframe, DailyTimeframe):
                assert timeframe.name == "daily"
                assert timeframe.keep == backup_spec["daily_keep"]
                assert timeframe.times == backup_spec["daily_times"]
            elif isinstance(timeframe, WeeklyTimeframe):
                assert timeframe.name == "weekly"
                assert timeframe.keep == backup_spec["weekly_keep"]
                assert timeframe.times == backup_spec["weekly_times"]
                assert timeframe.weekdays == backup_spec["weekly_days"]
            elif isinstance(timeframe, MonthlyTimeframe):
                assert timeframe.name == "monthly"
                assert timeframe.keep == backup_spec["monthly_keep"]
                assert timeframe.times == backup_spec["monthly_times"]
                assert timeframe.monthdays == backup_spec["monthly_days"]
            else: # isinstance(timeframe, YearlyTimeframe):
                assert timeframe.name == "yearly"
                assert timeframe.keep == backup_spec["yearly_keep"]
                assert timeframe.times == backup_spec["yearly_times"]
                assert timeframe.yeardays == backup_spec["yearly_days"]

def test_TimeframeSchema_schema(valid_raw_config):
    for backup_name in sorted(valid_raw_config.keys()):
        backup_settings = valid_raw_config[backup_name]
        schema = config.TimeframeSchema.schema()
        processed_backup = schema(copy.deepcopy(backup_settings))
        for key in backup_settings:
            if isinstance(backup_settings[key], list):
                assert len(processed_backup[key]) == len(backup_settings[key])

        tf_types = Timeframe.tframe_types()
        tf_names = Timeframe.tframe_types(names=True)
        expected_tf_types = [tf_types[i]
                             for i in range(len(tf_names))
                             if tf_names[i] in backup_settings["timeframes"]]
        actual_tf_types = list(map(type, processed_backup["timeframes"]))
        unmodified_setting_keys = ["5minute_keep", "hourly_keep", "hourly_minutes", "daily_keep",
                                   "weekly_keep", "weekly_days", "monthly_keep", "monthly_days",
                                   "yearly_keep", "yearly_days"]
        times_settings = ["daily_times", "weekly_times", "monthly_times", "yearly_times"]
        for tf_type in expected_tf_types:
            assert tf_type in actual_tf_types
        for setting in unmodified_setting_keys:
            if setting in backup_settings:
                assert processed_backup[setting] == backup_settings[setting]
        for setting in times_settings:
            if setting in backup_settings:
                assert [isinstance(item, int)
                        for time in processed_backup[setting] for item in time]

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

def test_SrcDirDstDirSchema_dict_promote_ssh_target_spec_to_ssh_target(path_generator):
    src_dir = path_generator("src_dir", mkdir=True)
    dst_dir = path_generator("dst_dir", mkdir=True)
    data = {"src_dir": src_dir, "dst_dir": dst_dir}
    assert config.SrcDirDstDirSchema._dict_promote_ssh_target_spec_to_ssh_target(data) == data

    sshtarget_spec = "ssh://p22:root@localhost:/"
    fake_key = path_generator("fake_key", touch=True)
    data = {"src_dir": sshtarget_spec, "dst_dir": dst_dir, "ssh_key": fake_key}
    data = config.SrcDirDstDirSchema._dict_promote_ssh_target_spec_to_ssh_target(data)
    assert isinstance(data["src_dir"], SSHTarget)

    data = {"src_dir": src_dir, "dst_dir": sshtarget_spec, "ssh_key": fake_key}
    data = config.SrcDirDstDirSchema._dict_promote_ssh_target_spec_to_ssh_target(data)
    assert isinstance(data["dst_dir"], SSHTarget)

def test_SrcDirDstDirSchema_dict_ssh_target_connectable(sshtarget_generator, path_generator):
    sshtarget = sshtarget_generator()
    data = {"src_dir": Path("/foo"), "dst_dir": sshtarget, "ssh_key": sshtarget.key}
    data = config.SrcDirDstDirSchema._dict_ssh_target_connectable(data)
    assert isinstance(data["dst_dir"], SSHTarget)

    sshtarget = sshtarget_generator()
    with pytest.raises(vlp.Invalid) as exc:
        bad_key = path_generator("bad_key", touch=True)
        new_sshtarget = sshtarget.with_path(sshtarget.path)
        new_sshtarget.key = bad_key
        data = {"ssh_key": bad_key, "src_dir": Path("/foo"), "dst_dir": new_sshtarget}
        config.SrcDirDstDirSchema._dict_ssh_target_connectable(data)
    assert str(exc.value) == config.SrcDirDstDirSchema.ErrMsg.SSH_CONNECTION_FAILED_TO_ESTABLISH

    with pytest.raises(vlp.Invalid) as exc:
        bad_dir = path_generator("bad_dir", mkdir=False)
        new_sshtarget = sshtarget.with_path(bad_dir)
        data = {"src_dir": new_sshtarget, "dst_dir": "/foo"}
        config.SrcDirDstDirSchema._dict_ssh_target_connectable(data)
    assert str(exc.value) == config.SrcDirDstDirSchema.ErrMsg.REMOTE_DIR_INVALID

def test_SrcDirDstDirSchema_schema(path_generator):
    schema = config.SrcDirDstDirSchema.schema()

    sshtarget_spec = "ssh://p22:root@localhost:/"
    key = str(path_generator("key", touch=True))
    ssh_config = str(path_generator("ssh_config", touch=True))
    src_dir_str = str(path_generator("src_dir", mkdir=True))
    dst_dir_str = str(path_generator("dst_dir", mkdir=True))

    data = {"src_dir": src_dir_str, "dst_dir": dst_dir_str}
    assert schema(data) == {"src_dir": Path(src_dir_str), "dst_dir": Path(dst_dir_str)}

    data = {"src_dir": src_dir_str, "dst_dir": sshtarget_spec, "ssh_key": key, "ssh_config": ssh_config}
    data = schema(data)
    assert sorted(data) == ["dst_dir", "src_dir", "ssh_config", "ssh_key"]
    assert data["src_dir"] == Path(src_dir_str)
    assert isinstance(data["dst_dir"], SSHTarget)
    assert data["dst_dir"].path == Path("/")
    assert data["dst_dir"].port == 22
    assert data["dst_dir"].user == "root"
    assert data["dst_dir"].host == "localhost"
    assert data["dst_dir"].key == Path(key)
    assert data["dst_dir"].sshconfig == Path(ssh_config)
    assert data["ssh_key"] == Path(key)
    assert data["ssh_config"] == Path(ssh_config)

    data = {"src_dir": sshtarget_spec, "dst_dir": dst_dir_str, "ssh_key": key}
    data = schema(data)
    assert sorted(data) == ["dst_dir", "src_dir", "ssh_key"]
    assert data["dst_dir"] == Path(dst_dir_str)
    assert isinstance(data["src_dir"], SSHTarget)
    assert data["ssh_key"] == Path(key)

    data = {"foo": "bar", "src_dir": src_dir_str, "dst_dir": dst_dir_str}
    schema(data) # doesn't raise error for extra keys

    with pytest.raises(vlp.Invalid) as exc:
        data = {"src_dir": src_dir_str}
        schema(data)
    assert re.match("required key not provided", str(exc.value))

    with pytest.raises(vlp.Invalid) as exc:
        data = ["src_dir", src_dir_str, "dst_dir", dst_dir_str]
        schema(data)
    assert re.match("expected a dictionary", str(exc.value))

    Path(src_dir_str).rmdir()
    with pytest.raises(vlp.Invalid) as exc:
        data = {"src_dir": src_dir_str, "dst_dir": dst_dir_str}
        schema(data)
    assert re.match(config.SrcDirDstDirSchema.ErrMsg.NOT_VALID_SSHTARGET_SPEC_AND_NOT_VALID_LOCAL_DIR, str(exc.value))
    Path(src_dir_str).mkdir()

    Path(dst_dir_str).rmdir()
    with pytest.raises(vlp.Invalid) as exc:
        data = {"src_dir": src_dir_str, "dst_dir": dst_dir_str}
        schema(data)
    assert re.match(config.SrcDirDstDirSchema.ErrMsg.NOT_VALID_SSHTARGET_SPEC_AND_NOT_VALID_LOCAL_DIR, str(exc.value))
    Path(dst_dir_str).mkdir()

    Path(key).unlink()
    with pytest.raises(vlp.Invalid) as exc:
        data = {"src_dir": src_dir_str, "dst_dir": dst_dir_str, "ssh_key": key}
        schema(data)
    assert re.match(config.Schema.ErrMsg.LOCAL_FILE_INVALID, str(exc.value))
    Path(key).touch()

    Path(ssh_config).unlink()
    with pytest.raises(vlp.Invalid) as exc:
        data = {"src_dir": src_dir_str, "dst_dir": dst_dir_str, "ssh_config": ssh_config}
        schema(data)
    assert re.match(config.Schema.ErrMsg.LOCAL_FILE_INVALID, str(exc.value))
    Path(ssh_config).touch()

    with pytest.raises(vlp.Invalid) as exc:
        data = {"src_dir": sshtarget_spec, "dst_dir": sshtarget_spec, "ssh_key": key}
        schema(data)
    assert str(exc.value) == config.SrcDirDstDirSchema.ErrMsg.MULTIPLE_SSH_TARGET_SPECS

    with pytest.raises(vlp.Invalid) as exc:
        data = {"src_dir": src_dir_str, "dst_dir": sshtarget_spec}
        schema(data)
    assert str(exc.value) == config.SrcDirDstDirSchema.ErrMsg.SSH_KEY_MISSING

def test_SrcDirDstDirSchema_schema_extra(sshtarget_generator, path_generator):
    schema_extra = config.SrcDirDstDirSchema.schema_extra()
    src_dir = path_generator("src_dir", mkdir=True)
    dst_dir = path_generator("dst_dir", mkdir=True)
    sshtarget = sshtarget_generator()

    data = {"src_dir": src_dir, "dst_dir": dst_dir}
    assert schema_extra(data) == data

    data = {"src_dir": sshtarget, "dst_dir": dst_dir}
    assert schema_extra(data) == data

    data = {"src_dir": src_dir, "dst_dir": sshtarget}
    assert schema_extra(data) == data

    with pytest.raises(vlp.Invalid) as exc:
        bad_key = path_generator("bad_key", touch=True)
        new_sshtarget = sshtarget_generator()
        new_sshtarget.key = bad_key
        data = {"src_dir": new_sshtarget, "dst_dir": dst_dir}
        schema_extra(data)
    assert str(exc.value) == config.SrcDirDstDirSchema.ErrMsg.SSH_CONNECTION_FAILED_TO_ESTABLISH

    with pytest.raises(vlp.Invalid) as exc:
        bad_path = path_generator("bad_path")
        new_sshtarget = sshtarget.with_path(sshtarget.path)
        new_sshtarget.path = bad_path
        data = {"src_dir": new_sshtarget, "dst_dir": dst_dir}
        schema_extra(data)
    assert str(exc.value) == config.SrcDirDstDirSchema.ErrMsg.REMOTE_DIR_INVALID

def test_BackupSchema_ensure_single_backup():
    d = {'foo': 1}
    assert d == config.BackupSchema._ensure_single_backup(d)
    d = {'foo': {'bar': 1, 'baz': 2}}
    assert d == config.BackupSchema._ensure_single_backup(d)
    d = {'foo': 1, 'bar': 2}
    with pytest.raises(vlp.Invalid) as exc:
        config.BackupSchema._ensure_single_backup(d)
    assert str(exc.value) == config.BackupSchema.ErrMsg.NOT_1_BACKUP
    d = {}
    with pytest.raises(vlp.Invalid) as exc:
        config.BackupSchema._ensure_single_backup(d)
    assert str(exc.value) == config.BackupSchema.ErrMsg.NOT_1_BACKUP

def test_BackupSchema_ensure_backup_name_valid():
    d = {'foo': {'bar': 1, 'baz': 2}}
    assert d == config.BackupSchema._ensure_backup_name_valid(d)
    d = {'': {'bar': 1, 'baz': 2}}
    with pytest.raises(vlp.Invalid) as exc:
        config.BackupSchema._ensure_backup_name_valid(d)
    assert str(exc.value) == config.BackupSchema.ErrMsg.INVALID_BACKUP_NAME
    d = {'foo/bar': {'bar': 1, 'baz': 2}}
    with pytest.raises(vlp.Invalid) as exc:
        config.BackupSchema._ensure_backup_name_valid(d)
    assert str(exc.value) == config.BackupSchema.ErrMsg.INVALID_BACKUP_NAME

def test_BackupSchema_apply_sub_schemas(valid_raw_config, path_generator):
    # success tests
    for backup_name in sorted(valid_raw_config.keys()):
        backup_spec = copy.deepcopy({ backup_name : valid_raw_config[backup_name] })
        backup_spec = config.BackupSchema._apply_sub_schemas(backup_spec)
        backup_settings = backup_spec[backup_name]
        assert isinstance(backup_settings["src_dir"], Path) or isinstance(backup_settings["src_dir"], SSHTarget)
        assert isinstance(backup_settings["dst_dir"], Path) or isinstance(backup_settings["dst_dir"], SSHTarget)
        assert isinstance(backup_settings["backend"], BackendBase)  # Changed from issubclass
        for tf in backup_settings["timeframes"]:
            assert isinstance(tf, Timeframe)
            # failure tests
    for backup_name in sorted(valid_raw_config.keys()):
        backup_spec = copy.deepcopy({ backup_name : valid_raw_config[backup_name] })
        backup_spec[backup_name]["backend"] = "INVALIDBACKEND"
        backup_spec[backup_name]["src_dir"] = path_generator("non-existent-path", touch=False, mkdir=False)
        backup_spec[backup_name]["timeframes"].append("INVALIDTIMEFRAMENAME")
        with pytest.raises(vlp.MultipleInvalid) as exc:
            backup_spec = config.BackupSchema._apply_sub_schemas(backup_spec)
        assert len(exc.value.errors) == 3

def test_BackupSchema_promote_to_backup_object(valid_raw_config):
    backup_name = random.choice(list(valid_raw_config.keys()))
    backup = config.BackupSchema._promote_to_backup_object({backup_name : valid_raw_config[backup_name]})
    assert isinstance(backup, bckp.Backup)

def test_BackupSchema_schema(valid_raw_config, path_generator):
    schema = config.BackupSchema.schema()
    # success tests
    for backup_name in sorted(valid_raw_config.keys()):
        backup_spec = { backup_name : valid_raw_config[backup_name] }
        backup = schema(backup_spec)
        assert isinstance(backup, bckp.Backup)
        assert backup.name == backup_name
        for tframe in backup.timeframes:
            assert isinstance(tframe, Timeframe)
        assert backup.backend.name() == backup_spec[backup_name]["backend"].name()
        if backup.backup_type == "local_to_local":
            assert isinstance(backup.src_dir, Path)
            assert isinstance(backup.dst_dir, Path)
        elif backup.backup_type == "local_to_remote":
            assert isinstance(backup.src_dir, Path)
            assert isinstance(backup.dst_dir, SSHTarget)
        else: # backup.backup_type == "remote_to_local"
            assert isinstance(backup.src_dir, SSHTarget)
            assert isinstance(backup.dst_dir, Path)
        raw_config_copy = copy.deepcopy(valid_raw_config)
        raw_config_copy[backup_name]["INVALIDSETTING"] = "foo"
        assert schema({ backup_name : raw_config_copy[backup_name] }) # no error for invalid setting
    # failure tests
    with pytest.raises(vlp.Invalid) as exc:
        schema({})
    assert str(exc.value) == config.BackupSchema.ErrMsg.NOT_1_BACKUP
    with pytest.raises(vlp.Invalid) as exc:
        schema(valid_raw_config)
    assert str(exc.value) == config.BackupSchema.ErrMsg.NOT_1_BACKUP
    for backup_name in sorted(valid_raw_config.keys()):
        raw_config_copy = copy.deepcopy(valid_raw_config)
        with pytest.raises(vlp.Invalid) as exc:
            schema({ f"{backup_name} &" : valid_raw_config[backup_name] })
        assert str(exc.value) == config.BackupSchema.ErrMsg.INVALID_BACKUP_NAME
        with pytest.raises(vlp.Invalid) as exc:
            raw_config_copy = copy.deepcopy(valid_raw_config)
            backup_settings = raw_config_copy[backup_name]
            backup_settings["backend"] = "INVALIDBACKENDNAME"
            schema({ backup_name: backup_settings })
        assert str(exc.value).startswith(config.BackendSchema.ErrMsg.INVALID_BACKEND_NAME)
        with pytest.raises(vlp.Invalid) as exc:
            raw_config_copy = copy.deepcopy(valid_raw_config)
            backup_settings = raw_config_copy[backup_name]
            backup_settings["timeframes"].append("INVALIDTIMEFRAME")
            schema({ backup_name: backup_settings })
        assert str(exc.value).startswith("value must be one of ['5minute',")
        with pytest.raises(vlp.Invalid) as exc:
            raw_config_copy = copy.deepcopy(valid_raw_config)
            backup_settings = raw_config_copy[backup_name]
            backup_settings["src_dir"] = path_generator("non-existent-dir", mkdir=False)
            schema({ backup_name: backup_settings })
        assert str(exc.value).startswith(config.SrcDirDstDirSchema.ErrMsg.NOT_VALID_SSHTARGET_SPEC_AND_NOT_VALID_LOCAL_DIR)

def test_parse_config(path_generator, valid_config_file_generator):
    backups = config.parse_config(valid_config_file_generator())
    assert len(backups) == 3
    for backup in backups:
        isinstance(backup, bckp.Backup)

    config_file_copy = path_generator("config-file-copy.yml", touch=False)
    shutil.copy(valid_config_file_generator(), config_file_copy)
    with open(config_file_copy, 'a') as f:
        f.write("invalid yaml syntax")
    with pytest.raises(config.ConfigErrors) as exc:
        config.parse_config(config_file_copy)
    assert len(exc.value.errors) == 1
    assert isinstance(exc.value.errors[0], yaml.YAMLError)

    empty_file = path_generator("empty-config-file", touch=True)
    with pytest.raises(config.ConfigErrors) as exc:
        config.parse_config(empty_file)
    assert isinstance(exc.value, config.ConfigErrors)

    config_file_invalid = path_generator("config-file-invalid.yml", touch=True)
    with open(valid_config_file_generator(num_backups=2), 'r') as fr, open(config_file_invalid, 'a') as fw:
        for line in fr:
            if line.startswith("  src_dir:"):
                invalid_path = path_generator("non-existent-path", touch=False)
                fw.write(f"  src_dir: {invalid_path}\n")
            elif line.startswith("  backend:"):
                fw.write("  backend: INVALIDBACKEND\n")
            elif line.startswith("  dst_dir:"):
                fw.write("")
            else:
                fw.write(line)
    with open(config_file_invalid, 'r') as f:
        print(f.read())

    with pytest.raises(config.ConfigErrors) as exc:
        config.parse_config(config_file_invalid)
    assert len(exc.value.errors) == 6
    for exc in exc.value.errors:
        backup_name, err = exc
        assert backup_name.startswith("backup_")
        assert isinstance(err, vlp.Invalid)

    with pytest.raises(config.ConfigErrors) as exc:
        config.parse_config(config_file_copy)
    assert len(exc.value.errors) == 1
    assert isinstance(exc.value.errors[0], yaml.YAMLError)
