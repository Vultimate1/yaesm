import yaml

from yaesm.backup import *
from yaesm.sshtarget import *
from yaesm.timeframe import *


class ConfigException(Exception):
    pass


class BadSpec():
    def __init__(self, where, what):
        self.where = where
        self.what = what


def append_missing_keys(l, d, keys) -> int:
    """Modifies `l` by appending all `keys` missing in `d` to the collection.

    Returns the number of additions added to the list."""
    missing = [k for k in keys if k not in d]
    l.extend(missing)
    return len(missing)

def construct_timeframes(backup_spec, timeframe_type) -> dict:
    """Returns a dictionary with 3 keys: `"timeframes"`, `"bad_specs"`, and
    `"missing_specs"`"""
    result = {"timeframes": [], "missing_specs": [], "bad_specs": []}
    validity_checks = {"keep" : Timeframe.valid_keep,
                       "minutes" : Timeframe.valid_minute,
                       "times" : Timeframe.valid_timespec,
                       "weekly_days" : Timeframe.valid_weekday,
                       "monthly_days" : Timeframe.valid_monthday,
                       "yearly_days" : Timeframe.valid_yearday}

    def check_validity(func, s):
        s = s if isinstance(s, list) else [s]
        for val in s:
            print(val)
            if not func(val):
                result["bad_specs"].append(BadSpec(setting, val))

    settings = timeframe_type.required_config_settings()
    if append_missing_keys(result["missing_specs"], backup_spec, settings) == 0:
        for setting in settings:
            setting_type = setting[setting.rfind("_") + 1:]
            if setting_type == "days":
                check_validity(validity_checks[setting], backup_spec[setting])
            else:
                check_validity(validity_checks[setting_type], backup_spec[setting])
        result["timeframes"].append(timeframe_type(*[backup_spec[s] for s in settings]))

    return result

def handle_timeframes(backup_spec) -> list:
    """Returns 3 lists containing the successfully instantiated timeframes, missing
    specifications and bad specifications, in that order."""
    timeframes = []
    bad_specs = []
    missing_specs = []
    if type(backup_spec["timeframes"]) is list:
        timeframe_dict = dict(zip(Timeframe.tframe_types(names=True), Timeframe.tframe_types()))
        for timeframe in backup_spec["timeframes"]:
            try:
                result = construct_timeframes(backup_spec, timeframe_dict[timeframe])
                timeframes.extend(result["timeframes"])
                missing_specs.extend(result["missing_specs"])
                bad_specs.extend(result["bad_specs"])
            except KeyError:
                bad_specs.append(BadSpec("timeframes", timeframe))
    else:
        bad_specs.append(BadSpec("timeframes", type(backup_spec["timeframes"])))

    return timeframes, missing_specs, bad_specs

def construct_backup(backup_name, backup_spec, timeframes):
    """Returns a result code and the type of data corresponding to said code.
    Whether specified directories exist or not is not checked.
    
    0 = `Backup` (success)
    
    1 = missing specifications (failure)
    
    2 = bad specifications (failure)"""
    if backup_spec["backend"] == "zfs":
        target_setting_names = ["src_dataset", "dst_dataset"]
    else:
        target_setting_names = ["src_dir", "dst_dir"]

    # Handle required specs
    result = []
    src = None
    dst = None
    if append_missing_keys(result, backup_spec, target_setting_names) == 0:
        src = backup_spec[target_setting_names[0]]
        dst = backup_spec[target_setting_names[1]]
        src_is_ssh = SSHTarget.is_sshtarget(src)
        dst_is_ssh = SSHTarget.is_sshtarget(dst)
        if src_is_ssh and dst_is_ssh:
            result.append(BadSpec(target_setting_names[0], src_is_ssh))
        elif src_is_ssh or dst_is_ssh:
            if append_missing_keys(result, backup_spec, ["ssh_key"]) == 0:
                ssh_key = backup_spec["ssh_key"]
                ssh_config = backup_spec.get("ssh_config")
                if src_is_ssh:
                    src = SSHTarget(src, ssh_key, sshconfig=ssh_config)
                if dst_is_ssh:
                    dst = SSHTarget(dst, ssh_key, sshconfig=ssh_config)
            else:
                return 1, result
    else:
        return 1, result

    # Construct backup & handle optional specs
    if backup_spec["backend"] == "rsync":
        if (exclude := backup_spec.get("exclude", False)) and type(exclude) is not list:
            result.append(BadSpec("exclude", type(exclude)))
        if (exclude_from := backup_spec.get("exclude_from", False)) \
            and type(exclude_from) is not str:
            result.append(BadSpec("exclude_from", type(exclude_from)))
        if len(result) == 0:
            return 0, Backup(backup_name, src, dst, timeframes)
            # TODO:
            # return 0, Backup(backup_name, src, dst, timeframes, exclude=exclude,
            #                  exclude_from=exclude_from)
    elif backup_spec["backend"] == "btrfs" or backup_spec["backend"] == "zfs":
        if len(result) == 0:
            return 0, Backup(backup_name, src, dst, timeframes)
    else:
        result.append(BadSpec("backend", backup_spec["backend"]))

    return 2, result


def parse_file(config_path) -> list:
    """Returns a list of backups, given a valid YAML config file.

    Throws `OSError` if unable to open `config_path`,
    `yaml.YAMLError` if the file is malformed, and
    `yaesm.ConfigException` on invalid input.

    All missing or invalid specs should be logged (also TODO)"""
    backups = []
    invalid_input_info = {}
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
        for backup_name, backup_spec in data.items():
            missing_specs = []
            bad_specs = []
            if append_missing_keys(missing_specs, backup_spec, ["backend", "timeframes"]) > 0:
                invalid_input_info[backup_name] = [missing_specs, bad_specs]
                continue

            timeframes, temp, bad_specs = handle_timeframes(backup_spec)
            missing_specs.extend(temp)
            code, result = construct_backup(backup_name, backup_spec, timeframes)
            match code:
                case 0:
                    backups.append(result)
                case 1:
                    missing_specs.extend(result)
                case 2:
                    bad_specs.extend(result)

            if len(missing_specs) > 0 or len(bad_specs) > 0:
                invalid_input_info[backup_name] = [missing_specs, bad_specs]

    if len(invalid_input_info) > 0:
        # TEMP
        for name, info in invalid_input_info.items():
            for missing in info[0]:
                print(name + ": " + missing)
            for bad in info[1]:
                print(name + ": " + bad.where + ", " + str(bad.what))

        raise ConfigException

    return backups
