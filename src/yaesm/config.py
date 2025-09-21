"""src/yaesm/config.py"""
from typing import final
from datetime import datetime
import re
import yaml
import voluptuous as vlp
from pathlib import Path

from yaesm.backup import Backup
from yaesm.sshtarget import SSHTarget
from yaesm.timeframe import Timeframe

class Schema():
    """Base class for all yaesm configuration schema classes."""

    class ErrMsg:
        LOCAL_DIR_INVALID = "Not a complete path to an existing directory"
        LOCAL_FILE_INVALID = "Not a complete path to an existing file"

    @staticmethod
    def schema() -> vlp.Schema:
        """The base schema is responsible for doing basic and safe (non-IO)
        validation and for coercing freshly parsed yaml into usable types.
        """
        ...

    @staticmethod
    def schema_extra() -> vlp.Schema:
        """Extra schema to be only be run in some circumstances. This schema
        should only be applied to data after first applying the 'base_schema'
        """
        return Schema.schema_empty()

    @staticmethod
    @final
    def schema_empty() -> vlp.Schema:
        """A pass-through schema that accepts any input and returns it back unchanged."""
        return vlp.Schema(lambda x: x)

    @staticmethod
    def is_file(s:(Path | str)) -> Path:
        """Validator to ensure 's' is a string or Path representing a full path
        to a regular file (not directory) on the system, and if so returns 's'
        as a Path.
        """
        if not s or str(s)[0] != "/" or not Path(s).is_file():
            raise vlp.Invalid(Schema.ErrMsg.LOCAL_FILE_INVALID)
        return Path(s)

    @staticmethod
    def is_dir(s:(Path | str)) -> Path:
        """Validator to ensure 's' is a string or Path representing a full path
        to an existing directory on the system, and if so returns 's' as a Path.
        """
        if not s or str(s)[0] != "/" or not Path(s).is_dir():
            raise vlp.Invalid(Schema.ErrMsg.LOCAL_DIR_INVALID)
        return Path(s)

class TimeframeSchema(Schema):
    """TODO"""
    class ErrMsg:
        SETTING_MISSING = "A setting required by one of your timeframe types is missing"
        TIME_MALFORMED = "Not a valid time specification"
        HOUR_OUT_OF_RANGE = "Hour portion of time specification not within range [0, 23]"
        MINUTE_OUT_OF_RANGE = "Minute portion of time specification not within range [0, 59]"

    @staticmethod
    def get_days_in_year(year):
        """Returns 366 if `year` is a leap year, and 365 otherwise."""
        if year % 4 != 0 or (year % 100 == 0 and year % 400 != 0):
            return 365
        return 366

    @staticmethod
    def timeframe_schema() -> vlp.Schema:
        """Voluptuous Schema to validate timeframe configs."""
        return vlp.Schema(
            vlp.All(
                {vlp.Required("timeframes"): vlp.All(list,
                                                     ["5minute", "hourly", "daily", "weekly",
                                                      "monthly", "yearly"])},
                TimeframeSchema.has_required_settings,
                {("5minute_keep", "hourly_keep", "daily_keep", "weekly_keep", "monthly_keep",
                  "yearly_keep"): vlp.All(int, vlp.Range(min=0)),
                  "hourly_minutes": vlp.All(int, vlp.Range(min=0, max=59)),
                  ("daily_times", "weekly_times", "monthly_times",
                   "yearly_times"): vlp.All(TimeframeSchema.are_valid_timespecs,
                                            TimeframeSchema.are_valid_hours,
                                            TimeframeSchema.are_valid_minutes),
                  "weekly_days": ["monday", "tuesday", "wednesday", "thursday", "friday",
                                 "saturday", "sunday"],
                  "monthly_days": vlp.All(int, vlp.Range(min=1, max=31)),
                  "yearly_days": vlp.All(int,
                                         vlp.Range(min=1,
                                                   max=TimeframeSchema.get_days_in_year(
                                                       datetime.now().year)))}))

    @staticmethod
    def has_required_settings(spec: dict) -> dict:
        """TODO"""
        required_settings = {
            "5minute": ["5minute_keep"],
            "hourly": ["hourly_keep", "hourly_minutes"],
            "daily": ["daily_keep", "daily_times"],
            "weekly": ["weekly_keep", "weekly_times", "weekly_days"],
            "monthly": ["monthly_keep", "monthly_times", "monthly_days"],
            "yearly": ["yearly_keep", "yearly_times", "yearly_days"]
        }
        for tf_type in spec["timeframes"]:
            missing_settings = [setting not in spec.keys()
                                for setting in required_settings[tf_type]]
            if len(missing_settings) > 0:
                raise vlp.Invalid(TimeframeSchema.ErrMsg.SETTING_MISSING
                                  + f"\n\t{tf_type}: {missing_settings}")
        return spec

    @staticmethod
    def are_valid_timespecs(spec: list[str]) -> list[list[int, int]]:
        """TODO"""
        res = []
        for timespec in spec:
            timespec_re = re.compile("([0-9]{2}):([0-9]{2})")
            if re_result := timespec_re.match(timespec):
                res.append([int(re_result.group(1)), int(re_result.group(2))])
            else:
                raise vlp.Invalid(TimeframeSchema.ErrMsg.TIME_MALFORMED
                                  + f"\n\tExpected format 'hh:mm', got {timespec}")
        return res

    @staticmethod
    def are_valid_hours(spec: list[list[int, int]]) -> list[list[int, int]]:
        """TODO"""
        for time in spec:
            if time[0] < 0 or time[0] > 23:
                raise vlp.Invalid(TimeframeSchema.ErrMsg.HOUR_OUT_OF_RANGE
                                  + f"\n\tGot {spec}")
        return spec

    @staticmethod
    def are_valid_minutes(spec: list[list[int, int]]) -> list[list[int, int]]:
        """TODO"""
        for time in spec:
            if time[1] < 0 or time[1] > 59:
                raise vlp.Invalid(TimeframeSchema.ErrMsg.MINUTE_OUT_OF_RANGE
                                  + f"\n\tGot {spec}")
        return spec


class SrcDirDstDirSchema(Schema):
    """Voluptuous schema and validator functions for a src_dir and dst_dir configuration."""

    class ErrMsg:
        REMOTE_DIR_INVALID = "Not a path to an existing directory at the SSH target remote path"
        REMOTE_FILE_INVALID = "Not a path to an existing file at the SSH target remote path"
        SSH_TARGET_SPEC_INVALID = "Not a valid SSH target spec"
        NOT_VALID_SSHTARGET_SPEC_AND_NOT_VALID_LOCAL_DIR = "Not an existing directory or a valid SSH target spec"
        MULTIPLE_SSH_TARGET_SPECS = "Both 'src_dir' and 'dst_dir' are SSH target specs"
        SSH_KEY_MISSING = "Failed to specify a 'ssh_key' which is required when using a SSH target"
        SSH_CONNECTION_FAILED_TO_ESTABLISH = "Could not establish an SSH connection to the SSH target"

    @staticmethod
    def schema() -> vlp.Schema:
        """Voluptuous Schema to validate a basic 'src_dir' and 'dst_dir' config.

        This Schema is meant to be applied to a dict whos values are still just
        strings (I.E. have been freshly parsed). The data structure returned by
        applying this schema is a dict that preserves the keys, but all dirs/files
        are mutated to pathlib.Path's, and a SSH target spec strings are mutated
        to an SSHTarget object.

        This schema implements the following error/sanity checks:
            * both 'src_dir' and 'dst_dir' are existing local directorys or SSH
              target spec strings

            * at most one of 'src_dir' or 'dst_dir' is an SSH target spec (yaesm
              does not support remote-to-remote backups)

            * if we are using an SSH target, ensure we were given an 'ssh_key'
              which is an existing local file.

            * Promotes strings denoting file paths to Path objects, and promotes
              SSH target spec strings to SSHTarget objects.
        """
        return vlp.Schema(
            vlp.All(
                { vlp.Required("src_dir"): SrcDirDstDirSchema._is_dir_or_sshtarget_spec,
                  vlp.Required("dst_dir"): SrcDirDstDirSchema._is_dir_or_sshtarget_spec,
                  vlp.Optional("ssh_key"): Schema.is_file,
                  vlp.Optional("ssh_config"): Schema.is_file
                },
                SrcDirDstDirSchema._dict_max_one_sshtarget_spec,
                SrcDirDstDirSchema._dict_ssh_key_required_if_ssh_target,
                SrcDirDstDirSchema._dict_promote_ssh_target_spec_to_ssh_target
            ),
            required=True)

    @staticmethod
    def schema_extra() -> vlp.Schema:
        """Ensure that if we are using an SSH target, that we can connect to it,
        and the remote directory being targeted exists on the remote server.
        """
        return vlp.Schema(vlp.All(SrcDirDstDirSchema._dict_ssh_target_connectable))

    @staticmethod
    def _is_sshtarget_spec(spec:str) -> str:
        """Validator to ensure 'spec' is a string representing a valid SSHTarget
        spec as per the function 'SSHTarget.is_sshtarget_spec()', and if so just
        returns 'spec' back directly.
        """
        if not spec or not SSHTarget.is_sshtarget_spec(spec):
            raise vlp.Invalid(SrcDirDstDirSchema.ErrMsg.SSH_TARGET_SPEC_INVALID)
        return spec

    @staticmethod
    def _is_dir_or_sshtarget_spec(s:str) -> (Path | str):
        """Validator to ensure 's' is a string representing either an existing
        directory on the system, or a valid SSH target spec.
        """
        validator = vlp.Any(
            SrcDirDstDirSchema._is_sshtarget_spec,
            Schema.is_dir,
            msg=SrcDirDstDirSchema.ErrMsg.NOT_VALID_SSHTARGET_SPEC_AND_NOT_VALID_LOCAL_DIR
        )
        return validator(s)

    @staticmethod
    def _dict_max_one_sshtarget_spec(d:dict) -> dict:
        """Ensure that the dict 'd' contains two keys, 'src_dir' and 'dst_dir'
        that both associate to Path's or SSH target specs, but ensure at most
        one of them is and SSH target spec.
        """
        if SSHTarget.is_sshtarget_spec(d["src_dir"]) and SSHTarget.is_sshtarget_spec(d["dst_dir"]):
            raise vlp.Invalid(SrcDirDstDirSchema.ErrMsg.MULTIPLE_SSH_TARGET_SPECS)
        return d

    @staticmethod
    def _dict_ssh_key_required_if_ssh_target(d:dict) -> dict:
        """Ensure that if either the key 'src_dir' or 'dst_dir' associates to
        an SSH target spec that we also have a key 'ssh_key' that associates to
        an existing file.
        """
        if SSHTarget.is_sshtarget_spec(d["src_dir"]) or SSHTarget.is_sshtarget_spec(d["dst_dir"]):
            if not d.get("ssh_key"):
                raise vlp.Invalid(SrcDirDstDirSchema.ErrMsg.SSH_KEY_MISSING)
            d["ssh_key"] = Schema.is_file(d["ssh_key"])
        return d

    @staticmethod
    def _dict_promote_ssh_target_spec_to_ssh_target(d:dict) -> dict:
        """Promotes an SSH target spec string to an actual SSHTarget object.
        This validator should only be called in a schema, after first calling
        `_dict_ssh_key_required_if_ssh_target` in a schema."""
        sshtarget_spec = None
        dir_key = None

        if SSHTarget.is_sshtarget_spec(d["src_dir"]):
            sshtarget_spec = d["src_dir"]
            dir_key = "src_dir"
        elif SSHTarget.is_sshtarget_spec(d["dst_dir"]):
            sshtarget_spec = d["dst_dir"]
            dir_key = "dst_dir"

        if sshtarget_spec and dir_key:
            ssh_key = d["ssh_key"]
            ssh_config = d.get("ssh_config")
            sshtarget = SSHTarget(sshtarget_spec, ssh_key, sshconfig=ssh_config)
            d[dir_key] = sshtarget

        return d

    @staticmethod
    def _dict_ssh_target_connectable(d:dict) -> dict:
        """Ensure that if an SSH target is being used, that we can establish a
        connection to the specified SSH server, and the target directory exists
        on the server. This validator should be called as a part of
        `schema_extra`, meaning it is only called on the output of the base
        schema.
        """
        sshtarget = None
        dir_key = None

        if isinstance(d["src_dir"], SSHTarget):
            sshtarget = d["src_dir"]
            dir_key = "src_dir"
        elif isinstance(d["dst_dir"], SSHTarget):
            sshtarget = d["dst_dir"]
            dir_key = "dst_dir"

        if sshtarget and dir_key:
            if not sshtarget.can_connect():
                raise vlp.Invalid(SrcDirDstDirSchema.ErrMsg.SSH_CONNECTION_FAILED_TO_ESTABLISH)
            if not sshtarget.is_dir(d=sshtarget.path):
                raise vlp.Invalid(SrcDirDstDirSchema.ErrMsg.REMOTE_DIR_INVALID)

        return d

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

def construct_timeframes(backup_spec, timeframe_type) -> Timeframe:
    """Returns a number of timeframes of `timeframe_type`."""
    settings = timeframe_type.required_config_settings()
    return timeframe_type(*[backup_spec[s] for s in settings])

def handle_timeframes(backup_spec) -> list:
    """Returns a list containing the successfully instantiated timeframes."""
    timeframes = []
    timeframe_dict = dict(zip(Timeframe.tframe_types(names=True), Timeframe.tframe_types()))
    for timeframe in backup_spec["timeframes"]:
        result = construct_timeframes(backup_spec, timeframe_dict[timeframe])
        timeframes.append(result)

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
