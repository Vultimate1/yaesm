import yaml
import voluptuous as vlp

from yaesm.backup import Backup
from yaesm.sshtarget import SSHTarget
from yaesm.timeframe import Timeframe

def path_or_sshtarget_validator():
    """A voluptuous validator accepting either a string representing an existing
    directory or an SSHTarget spec.
    """
    def sshtarget_spec(spec):
        if SSHTarget.is_sshtarget_spec(spec):
            return spec
        raise vlp.Invalid('not an SSH target spec')
    return vlp.Any(sshtarget_spec, vlp.IsDir())

def src_dir_dst_dir_schema(required=True):
    """Schema to validate src_dir and dst_dir settings, ensuring they aren't
    both sshtarget specs, and if they are local directories that they exist.
    """
    def not_both_ssh_target_specs(opts):
        if SSHTarget.is_sshtarget_spec(opts["src_dir"]) and SSHTarget.is_sshtarget_spec(opts["dst_dir"]):
            raise vlp.Invalid("'src_dir' and 'dst_dir' cannot both be SSH target specs")
        return opts

    def require_ssh_key_if_using_ssh_target(opts):
        has_ssh_target = SSHTarget.is_sshtarget_spec(opts["src_dir"]) or SSHTarget.is_sshtarget_spec(opts["dst_dir"])
        if has_ssh_target and "ssh_key" not in opts:
            raise vlp.Invalid("'ssh_key' is required when using an SSH target spec")

        return opts

    return vlp.Schema(vlp.All({
        "src_dir": path_or_sshtarget_validator(),
        "dst_dir": path_or_sshtarget_validator(),
        "ssh_key": vlp.Optional(vlp.IsFile()),
        "ssh_config": vlp.Optional(vlp.IsFile())
    }, not_both_ssh_target_specs, require_ssh_key_if_using_ssh_target),
    required=required)

def construct_timeframes(backup_spec, timeframe_type) -> list:
    """Returns a number of timeframes of `timeframe_type`."""
    result = []
    # validity_checks = {"keep" : Timeframe.valid_keep,
    #                    "minutes" : Timeframe.valid_minute,
    #                    "times" : Timeframe.valid_timespec,
    #                    "weekly_days" : Timeframe.valid_weekday,
    #                    "monthly_days" : Timeframe.valid_monthday,
    #                    "yearly_days" : Timeframe.valid_yearday}

    # def check_validity(func, s):
    #     s = s if isinstance(s, list) else [s]
    #     for val in s:
    #         if not func(val):
    #             result["bad_specs"].append(BadSpec(setting, val))

    settings = timeframe_type.required_config_settings()
    result.append(timeframe_type(*[backup_spec[s] for s in settings]))
    return result

def handle_timeframes(backup_spec) -> list:
    """Returns a list containing the successfully instantiated timeframes."""
    timeframes = []
    timeframe_dict = dict(zip(Timeframe.tframe_types(names=True), Timeframe.tframe_types()))
    for timeframe in backup_spec["timeframes"]:
        result = construct_timeframes(backup_spec, timeframe_dict[timeframe])
        timeframes.extend(result)

    return timeframes

def get_directories(backup_spec, target_setting_names) -> tuple[str | SSHTarget, str | SSHTarget]:
    """Returns the source and destination directories as a tuple, in that order.
    Additionally checks if either is an SSH target,"""
    src = backup_spec[target_setting_names[0]]
    dst = backup_spec[target_setting_names[1]]
    src_is_ssh = SSHTarget.is_sshtarget_spec(src)
    dst_is_ssh = SSHTarget.is_sshtarget_spec(dst)
    if src_is_ssh and dst_is_ssh:
        pass
    elif src_is_ssh or dst_is_ssh:
        ssh_key = backup_spec["ssh_key"]
        ssh_config = backup_spec.get("ssh_config")
        if src_is_ssh:
            src = SSHTarget(src, ssh_key, sshconfig=ssh_config)
        if dst_is_ssh:
            dst = SSHTarget(dst, ssh_key, sshconfig=ssh_config)
    return (src, dst)

def build_from_specs(backup_spec, backup_name, src, dst, timeframes) -> Backup:
    """Construct backup & handle optional specs. Returns `None` on failure."""
    if backup_spec["backend"] == "rsync":
        if (exclude := backup_spec.get("exclude", False)) and not isinstance(exclude, list):
            pass
        if (exclude_from := backup_spec.get("exclude_from", False)) \
            and not isinstance(exclude_from, str):
            pass
        return Backup(backup_name, src, dst, timeframes)
        # TODO:
        # return 0, Backup(backup_name, src, dst, timeframes, exclude=exclude,
        #                  exclude_from=exclude_from)
    if backup_spec["backend"] == "btrfs" or backup_spec["backend"] == "zfs":
        return Backup(backup_name, src, dst, timeframes)
    return None

def construct_backup(backup_name, backup_spec, timeframes) -> Backup:
    """Returns a `Backup` object construction was successful."""
    if backup_spec["backend"] == "zfs":
        target_setting_names = ["src_dataset", "dst_dataset"]
    else:
        target_setting_names = ["src_dir", "dst_dir"]
    src, dst = get_directories(backup_spec, target_setting_names)
    return build_from_specs(backup_spec, backup_name, src, dst, timeframes)

def parse_yaml_string(string: str) -> list:
    """Returns a list of backups, given a valid YAML string.

    Throws `yaml.YAMLError` if the file is malformed."""
    data = yaml.safe_load(string)
    backups = []
    for backup_name, backup_spec in data.items():
        timeframes = handle_timeframes(backup_spec)
        backups.append(construct_backup(backup_name, backup_spec, timeframes))
    return backups

def parse_file(config_path: str) -> list:
    """Returns a list of backups, given a valid YAML config file.

    Throws `OSError` if unable to open `config_path`"""
    with open(config_path, "r", encoding="utf-8") as f:
        return parse_yaml_string(f)


# def append_missing_keys(l, d, keys) -> int:
#     """Modifies `l` by appending all `keys` missing in `d` to the collection.

#     Returns the number of additions added to the list."""
#     missing = [k for k in keys if k not in d]
#     l.extend(missing)
#     return len(missing)

# def construct_timeframes(backup_spec, timeframe_type) -> dict:
#     """Returns a dictionary with 3 keys: `"timeframes"`, `"bad_specs"`, and
#     `"missing_specs"`"""
#     result = {"timeframes": [], "missing_specs": [], "bad_specs": []}
#     validity_checks = {"keep" : Timeframe.valid_keep,
#                        "minutes" : Timeframe.valid_minute,
#                        "times" : Timeframe.valid_timespec,
#                        "weekly_days" : Timeframe.valid_weekday,
#                        "monthly_days" : Timeframe.valid_monthday,
#                        "yearly_days" : Timeframe.valid_yearday}

#     def check_validity(func, s):
#         s = s if isinstance(s, list) else [s]
#         for val in s:
#             if not func(val):
#                 result["bad_specs"].append(BadSpec(setting, val))

#     settings = timeframe_type.required_config_settings()
#     if append_missing_keys(result["missing_specs"], backup_spec, settings) == 0:
#         for setting in settings:
#             setting_type = setting[setting.rfind("_") + 1:]
#             if setting_type == "days":
#                 check_validity(validity_checks[setting], backup_spec[setting])
#             else:
#                 check_validity(validity_checks[setting_type], backup_spec[setting])
#         result["timeframes"].append(timeframe_type(*[backup_spec[s] for s in settings]))

#     return result

# def handle_timeframes(backup_spec) -> list:
#     """Returns 3 lists containing the successfully instantiated timeframes, missing
#     specifications and bad specifications, in that order."""
#     timeframes = []
#     bad_specs = []
#     missing_specs = []
#     if type(backup_spec["timeframes"]) is list:
#         timeframe_dict = dict(zip(Timeframe.tframe_types(names=True), Timeframe.tframe_types()))
#         for timeframe in backup_spec["timeframes"]:
#             try:
#                 result = construct_timeframes(backup_spec, timeframe_dict[timeframe])
#                 timeframes.extend(result["timeframes"])
#                 missing_specs.extend(result["missing_specs"])
#                 bad_specs.extend(result["bad_specs"])
#             except KeyError:
#                 bad_specs.append(BadSpec("timeframes", timeframe))
#     else:
#         bad_specs.append(BadSpec("timeframes", type(backup_spec["timeframes"])))

#     return timeframes, missing_specs, bad_specs

# def construct_backup(backup_name, backup_spec, timeframes):
#     """Returns a result code and the type of data corresponding to said code.
#     Whether specified directories exist or not is not checked.
    
#     0 = `Backup` (success)
    
#     1 = missing specifications (failure)
    
#     2 = bad specifications (failure)"""
#     if backup_spec["backend"] == "zfs":
#         target_setting_names = ["src_dataset", "dst_dataset"]
#     else:
#         target_setting_names = ["src_dir", "dst_dir"]

#     # Handle required specs
#     result = []
#     src = None
#     dst = None
#     if append_missing_keys(result, backup_spec, target_setting_names) == 0:
#         src = backup_spec[target_setting_names[0]]
#         dst = backup_spec[target_setting_names[1]]
#         src_is_ssh = SSHTarget.is_sshtarget(src)
#         dst_is_ssh = SSHTarget.is_sshtarget(dst)
#         if src_is_ssh and dst_is_ssh:
#             result.append(BadSpec(target_setting_names[0], src_is_ssh))
#         elif src_is_ssh or dst_is_ssh:
#             if append_missing_keys(result, backup_spec, ["ssh_key"]) == 0:
#                 ssh_key = backup_spec["ssh_key"]
#                 ssh_config = backup_spec.get("ssh_config")
#                 if src_is_ssh:
#                     src = SSHTarget(src, ssh_key, sshconfig=ssh_config)
#                 if dst_is_ssh:
#                     dst = SSHTarget(dst, ssh_key, sshconfig=ssh_config)
#             else:
#                 return 1, result
#     else:
#         return 1, result

#     # Construct backup & handle optional specs
#     if backup_spec["backend"] == "rsync":
#         if (exclude := backup_spec.get("exclude", False)) and type(exclude) is not list:
#             result.append(BadSpec("exclude", type(exclude)))
#         if (exclude_from := backup_spec.get("exclude_from", False)) \
#             and type(exclude_from) is not str:
#             result.append(BadSpec("exclude_from", type(exclude_from)))
#         if len(result) == 0:
#             return 0, Backup(backup_name, src, dst, timeframes)
#             # TODO:
#             # return 0, Backup(backup_name, src, dst, timeframes, exclude=exclude,
#             #                  exclude_from=exclude_from)
#     elif backup_spec["backend"] == "btrfs" or backup_spec["backend"] == "zfs":
#         if len(result) == 0:
#             return 0, Backup(backup_name, src, dst, timeframes)
#     else:
#         result.append(BadSpec("backend", backup_spec["backend"]))

#     return 2, result

# def parse_file(config_path) -> list:
#     """Returns a list of backups, given a valid YAML config file.

#     Throws `OSError` if unable to open `config_path`,
#     `yaml.YAMLError` if the file is malformed, and
#     `yaesm.ConfigException` on invalid input.

#     All missing or invalid specs should be logged (also TODO)"""
    # backups = []
    # invalid_input_info = {}
    # with open(config_path, "r") as f:
    #     data = yaml.safe_load(f)
    #     for backup_name, backup_spec in data.items():
    #         missing_specs = []
    #         bad_specs = []
    #         if append_missing_keys(missing_specs, backup_spec, ["backend", "timeframes"]) > 0:
    #             invalid_input_info[backup_name] = [missing_specs, bad_specs]
    #             continue

    #         timeframes, temp, bad_specs = handle_timeframes(backup_spec)
    #         missing_specs.extend(temp)
    #         code, result = construct_backup(backup_name, backup_spec, timeframes)
    #         match code:
    #             case 0:
    #                 backups.append(result)
    #             case 1:
    #                 missing_specs.extend(result)
    #             case 2:
    #                 bad_specs.extend(result)

    #         if len(missing_specs) > 0 or len(bad_specs) > 0:
    #             invalid_input_info[backup_name] = [missing_specs, bad_specs]

    # if len(invalid_input_info) > 0:
    #     # TODO: Do logging here
    #     raise ConfigException

    # return backups
