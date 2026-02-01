"""src/yaesm/config.py"""
from typing import final
import re
from pathlib import Path

import yaml
import voluptuous as vlp

from yaesm.backend import backendbase
import yaesm.backup as bckp
from yaesm.sshtarget import SSHTarget
from yaesm.timeframe import Timeframe

class ConfigErrors(Exception):
    def __init__(self, config_file, errors):
        self.config_file = config_file
        self.errors = errors # list of pairs of backup-name, vlp.Invalid

def parse_config(config_file):
    """Parse the file `config_file` into a list of `Backup` objects. This is the
    only function that should be directly used from outside the yaesm.config module.
    If there are any configuration errors then a `ConfigErrors` exception is raised.
    """
    config_file = Path(config_file)
    if not config_file.is_file():
        raise ConfigErrors(config_file, [f"config file does not exist: {config_file}"])

    with open(config_file, 'r') as f:
        try:
            config_data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ConfigErrors(config_file, [exc])
    backup_names = sorted(list(config_data.keys())) if config_data else []
    if not backup_names:
        raise ConfigErrors(config_file, ["no backups specified"])
    backup_schema = BackupSchema.schema()
    backups = []
    errors = []
    for backup_name in backup_names:
        try:
            backup = backup_schema({backup_name: config_data[backup_name]})
            backups.append(backup)
        except vlp.MultipleInvalid as exc:
            for error in exc.errors:
                errors += [(backup_name, error)]
    if errors:
        raise ConfigErrors(config_file, errors)
    return backups

class Schema():
    """Base class for all yaesm configuration schema classes."""

    class ErrMsg:
        LOCAL_DIR_INVALID = "Not a complete path to an existing directory"
        LOCAL_FILE_INVALID = "Not a complete path to an existing file"

    @staticmethod
    def schema() -> vlp.Schema:
        """The base schema is responsible for doing basic validation and for
        coercing freshly parsed yaml into usable types.
        """
        ...

    @staticmethod
    def schema_extra() -> vlp.Schema:
        """Extra schema is only run in some circumstances. More complicated
        validation (like testing SSH connectivity) should happen in this schema.
        This schema should only be applied to data after first applying the 'base_schema'.
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

class BackupSchema(Schema):
    """BackupSchema is the top-level concrete Schema class."""

    class ErrMsg:
        NOT_1_BACKUP="Not given exactly 1 backup"
        INVALID_BACKUP_NAME="Not a valid backup name"

    @staticmethod
    def schema() -> vlp.Schema:
        """Voluptuous schema that combines together all the other sub-schemas
        to produce an actual `Backup` object. This is the only schema that is
        directly used by the `parse_config` function.
        """
        return vlp.Schema(vlp.All(
            dict,
            BackupSchema._ensure_single_backup,
            BackupSchema._ensure_backup_name_valid,
            BackupSchema._apply_sub_schemas,
            BackupSchema._promote_to_backup_object)
        )

    @staticmethod
    def _apply_sub_schemas(d:dict) -> dict:
        """Apply all of the sub schemas (TimeframeSchema, SrcDirDstDirSchema, etc)
        to `d`, mutating d. Collects all errors, and raises a vlp.MultipleInvalid
        exception with all found errors, if any. This function is also
        responsible for applying the proper backend-specific schema. Only call
        this function after ensuring `d` contains just a single backup.
        """
        backup_name = list(d.keys())[0]
        backup_settings = d[backup_name]
        errors = []
        for schema_class in [BackendSchema, SrcDirDstDirSchema, TimeframeSchema]:
            schema = schema_class.schema()
            try:
                backup_settings = schema(backup_settings)
                if schema_class == BackendSchema:
                    backend_schema = type(backup_settings["backend"]).config_schema()
                    backup_settings = backend_schema(backup_settings)
            except vlp.MultipleInvalid as exc:
                errors += exc.errors
            except vlp.Invalid as exc:
                errors.append(exc)
        if errors:
            raise vlp.MultipleInvalid(errors)
        d[backup_name] = backup_settings
        return d

    @staticmethod
    def _promote_to_backup_object(d:dict) -> bckp.Backup:
        """Promote the backup spec dictionary `d` to an actual `Backup`
        object. This function should only be called after all other validation
        has taken place to ensure we have a valid backup spec.
        """
        backup_name = list(d.keys())[0]
        backup_settings = d[backup_name]
        backend_obj = backup_settings["backend"]
        if backup_settings.get("extra_opts"):
            backend_obj.extra_opts = backup_settings["extra_opts"]
        timeframes = backup_settings["timeframes"]
        src_dir = backup_settings["src_dir"]
        dst_dir = backup_settings["dst_dir"]
        return bckp.Backup(backup_name, backend_obj, src_dir, dst_dir, timeframes)

    @staticmethod
    def _ensure_single_backup(d:dict):
        """Validator to ensure that `d` is a dict with a single key (i.e. just one backup)."""
        if not 1 == len(d):
            raise vlp.Invalid(BackupSchema.ErrMsg.NOT_1_BACKUP)
        return d

    @staticmethod
    def _ensure_backup_name_valid(d:dict):
        """Ensure that the single key in the dict `d` is a string denoting a valid
        backup name. Should only be called after first calling `_ensure_single_backup`.
        """
        backup_name = list(d.keys())[0]
        if not bckp.backup_name_valid(backup_name):
            raise vlp.Invalid(BackupSchema.ErrMsg.INVALID_BACKUP_NAME)
        return d

class BackendSchema(Schema):
    """Schema for ensuring a valid backend was specified, and if so promoting the
    backend name to an actual backend class.
    """
    class ErrMsg:
        INVALID_BACKEND_NAME="Not a valid backend name"

    @staticmethod
    def schema() -> vlp.Schema:
        """Schema that accepts a dict with a single key 'backend' with a value
        that is a string dentoting a valid backend name (like 'btrfs' or 'rsync').

        This schema Outputs a dict with the backend name promoted to its
        corresponding backend class.
        """
        return vlp.Schema(vlp.All({
            vlp.Required("backend"): vlp.In(
                [cls.name() for cls in backendbase.BackendBase.backend_classes()],
                msg=BackendSchema.ErrMsg.INVALID_BACKEND_NAME
            )},
            BackendSchema._dict_promote_backend_name_to_backend_instance,
            BackendSchema._apply_backend_specific_schema
            ), extra=vlp.ALLOW_EXTRA)

    @staticmethod
    def _dict_promote_backend_name_to_backend_instance(d:dict) -> dict:
        """Promotes a backend name to its corresponding backend class instance."""
        backend_name = d["backend"]
        for backend_class in backendbase.BackendBase.backend_classes():
            if backend_name == backend_class.name():
                d["backend"] = backend_class()  # Create an instance!
                break
        return d

    @staticmethod
    def _apply_backend_specific_schema(d:dict) -> dict:
        """"Apply the backend-specific configuration schema to the backup settings dict."""
        backend_instance = d["backend"]
        backend_schema = type(backend_instance).config_schema()
        d = backend_schema(d)
        return d

class TimeframeSchema(Schema):
    """Voluptuous schema and validator for timeframe configuration."""

    class ErrMsg:
        SETTING_MISSING = "A setting required by one of your timeframe types is missing"
        TIME_MALFORMED = "Not a valid time specification"
        HOUR_OUT_OF_RANGE = "Hour portion of time specification not within range [0, 23]"
        MINUTE_OUT_OF_RANGE = "Minute portion of time specification not within range [0, 59]"

    # Be Scared, BE AFRAID!!! Due to an oversight, devs must ensure the settings for
    # each key are in the same order as they appear in the Timeframe class'
    # constructor. Fix this when convenient.
    REQUIRED_SETTINGS = {
        "5minute": ["5minute_keep"],
        "hourly": ["hourly_keep", "hourly_minutes"],
        "daily": ["daily_keep", "daily_times"],
        "weekly": ["weekly_keep", "weekly_times", "weekly_days"],
        "monthly": ["monthly_keep", "monthly_times", "monthly_days"],
        "yearly": ["yearly_keep", "yearly_times", "yearly_days"]
    }

    @staticmethod
    def schema() -> vlp.Schema:
        """Voluptuous Schema to validate timeframe configs.

        This Schema is meant to be applied to a `dict` containing the freshly parsed values
        for the backup. This returns a `dict` which preserves the keys, but will modify any
        '*_times' settings to a pair of `int` representing the hour and minute. Assuming all
        prior tests pass, the ''timeframes' key will have its value replaced with a list of
        `Timeframe`.

        This schema implements the following checks:
            * 'timeframes' is a `list` and contains only valid timeframe types
            * for every type listed in 'timeframes', check that the required settings have been
              provided
            * all '*_keep' settings are an `int` and no less than 0
            * 'hourly_minutes' (if given) contains `int`'s within the range of 0-59, inclusive
            * all '*_times' settings contain correctly formatted timespecs (`hh-mm`), each with
              valid hour and minute part (this tool uses military time)
            * 'weekly_days' (if given) contains only valid weekdays (not case sensitive)
            * 'monthly_days' (if given) contains only days found within *any* month, that is,
              1-31. Be advised that months that do *not* contain a given day are skipped. For
              example: a timeframe with a month day of 29 only makes a backup in February on
              leap years.
            * 'yearly_days' (if given) contains a valid day within the range 1-365 (TODO: Add
              support for leap years with 366 days.)"""
        return vlp.Schema(
            vlp.All(
                {vlp.Required("timeframes"): vlp.All(list,
                    [vlp.All(vlp.In(["5minute", "hourly", "daily", "weekly", "monthly",
                                     "yearly"]))])},
                TimeframeSchema.has_required_settings,
                {("5minute_keep", "hourly_keep", "daily_keep", "weekly_keep", "monthly_keep",
                  "yearly_keep"): vlp.All(int, vlp.Range(min=0)),
                  "hourly_minutes": [vlp.All(int, vlp.Range(min=0, max=59))],
                  ("daily_times", "weekly_times", "monthly_times",
                   "yearly_times"): vlp.All(TimeframeSchema.are_valid_timespecs,
                                            TimeframeSchema.are_valid_hours,
                                            TimeframeSchema.are_valid_minutes),
                  "weekly_days": [vlp.All(vlp.Lower,
                                          vlp.In(["monday", "tuesday", "wednesday", "thursday",
                                                  "friday", "saturday", "sunday"]))],
                  "monthly_days": [vlp.All(int, vlp.Range(min=1, max=31))],
                  "yearly_days": [vlp.All(int, vlp.Range(min=1, max=365))]},
                TimeframeSchema._promote_timeframes_spec_to_list_of_timeframes),
                extra=vlp.ALLOW_EXTRA)

    @staticmethod
    def has_required_settings(spec: dict) -> dict:
        """Takes the `spec` passed to the schema.
        
        Raises a `voluptuous.Invalid` if not all the settings for the given timeframe
        types are present."""
        for tf_type in spec["timeframes"]:
            missing_settings = list(filter(lambda s: s not in spec.keys(),
                                           TimeframeSchema.REQUIRED_SETTINGS[tf_type]))
            if len(missing_settings) > 0:
                raise vlp.Invalid(TimeframeSchema.ErrMsg.SETTING_MISSING
                                  + f"\n\t{tf_type}: {missing_settings}")
        return spec

    @staticmethod
    def are_valid_timespecs(spec: list[str]) -> list[list[int, int]]:
        """Takes a list of supposed timespecs. Returns a list of hour-minute pairings if
        successful. This does NOT check if the hour and minute parts are valid, use
        `are_valid_hours` and `are_valid_minutes` to do this.

        Raises `voluptuous.Invalid` if a timespec is formatted incorrectly, or if the minute or
        hour parts cannot be converted to `int`."""
        res = []
        for timespec in spec:
            timespec_re = re.compile("^([0-9][0-9]?)-([0-9][0-9]?)$")
            if re_result := timespec_re.match(timespec):
                res.append([int(re_result.group(1)), int(re_result.group(2))])
            else:
                raise vlp.Invalid(TimeframeSchema.ErrMsg.TIME_MALFORMED
                                  + f"\n\tExpected format 'hh-mm', got {timespec}")
        return res

    @staticmethod
    def are_valid_hours(spec: list[list[int, int]]) -> list[list[int, int]]:
        """Takes a list of hour:minute pairings.
        
        Raises `voluptuous.Invalid` if the hour part is not within the accepted range."""
        for time in spec:
            if time[0] < 0 or time[0] > 23:
                raise vlp.Invalid(TimeframeSchema.ErrMsg.HOUR_OUT_OF_RANGE
                                  + f"\n\tGot {time}")
        return spec

    @staticmethod
    def are_valid_minutes(spec: list[list[int, int]]) -> list[list[int, int]]:
        """Takes a list of hour:minute pairings.

        Raises `voluptuous.Invalid` if the minute part is not within the accepted range."""
        for time in spec:
            if time[1] < 0 or time[1] > 59:
                raise vlp.Invalid(TimeframeSchema.ErrMsg.MINUTE_OUT_OF_RANGE
                                  + f"\n\tGot {time}")
        return spec

    @staticmethod
    def _promote_timeframes_spec_to_list_of_timeframes(spec: dict) -> dict:
        """Maintains the 'timeframes' key, but replaces its value with a list of
        `yaesm.Timeframe`.

        The value paired with 'timeframes' in `spec` is assumed to be entirely valid."""
        timeframe_dict = dict(zip(Timeframe.tframe_types(names=True), Timeframe.tframe_types()))
        timeframes = []
        for timeframe_name in spec["timeframes"]:
            timeframe_obj = timeframe_dict[timeframe_name](*[spec[s] for s in TimeframeSchema.REQUIRED_SETTINGS[timeframe_name]])
            timeframes.append(timeframe_obj)
        spec["timeframes"] = timeframes # mutation
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
            extra=vlp.ALLOW_EXTRA)

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
