import yaml

from yaesm.timeframe import *

def append_missing_keys(l, d, keys) -> int:
    """Modifies `l` by appending all `keys` missing in `d` to the collection.

    Returns the number of additions added to the list."""
    l.extend(filter(lambda k : k not in d, keys))

def get_line(token):
    """Returns the PyYAML token's line in the loaded file."""
    return token.start_mark.line + 1

def construct_timeframes(backup_spec, timeframe_type) -> list:
    """Returns a list beginning with a code signifying the content being
    returned, followed by the content itself:

    0 = Instantiated timeframes (success)
    
    1 = Missing specs (failure)
    
    2 = Bad specs (failure)"""
    result_code = 0
    result = {0 : [], 1 : [], 2 : []}
    settings = timeframe_type.required_config_settings()
    if append_missing_keys(result[1], backup_spec, settings) == 0:
        for setting in settings:
            setting_type = setting[setting.rfind("_") + 1:]
            match setting_type:
                case "keep":
                    if not Timeframe.valid_keep(backup_spec[setting]):
                        result[2].append([backup_spec[setting], backup_spec[setting]])
                case "minutes":
                    for minute in backup_spec[setting]:
                        if not Timeframe.valid_minute(minute):
                            result[2].append([backup_spec[setting], minute])
                case "times":
                    for time in backup_spec[setting]:
                        if not Timeframe.valid_timespec(time):
                            result[2].append([backup_spec[setting], time])
                case "days":
                    match setting:
                        case "weekly_days":
                            for day in backup_spec[setting]:
                                if not Timeframe.valid_weekday(day):
                                    result[2].append([backup_spec[setting], day])
                        case "monthly_days":
                            for day in backup_spec[setting]:
                                if not Timeframe.valid_monthday(day):
                                    result[2].append([backup_spec[setting], day])
                        case "yearly_days":
                            for day in backup_spec[setting]:
                                if not Timeframe.valid_yearday(day):
                                    result[2].append([backup_spec[setting], day])
    else:
        result_code = 1

    if len(result[2]) != 0:
        result_code = 2

    return [result_code] + result[result_code]

def handle_timeframes(missing_specs, bad_specs, backup_spec) -> list:
    """Returns a list of all successfully instantiated timeframes."""
    timeframes = []
    if backup_spec["timeframes"] is list:
        timeframe_dict = dict(zip(Timeframe.tframe_types(names=True), Timeframe.tframe_types()))
        for timeframe in backup_spec["timeframes"]:
            try:
                result = construct_timeframes(backup_spec, timeframe_dict[timeframe])
                match result[0]:
                    case 0:
                        timeframes.extend(result[1:])
                    case 1:
                        missing_specs.extend(result[1:])
                    case 2:
                        bad_specs["timeframes"].extend(result[1:])
            except KeyError:
                bad_specs["timeframes"].append([get_line(backup_spec["timeframes"]), timeframe])
    else:
        bad_specs["timeframes"] = [get_line(backup_spec), None]

    return timeframes

def parse_file(config_path):
    """Returns a list of backups, given a valid YAML config file.

    Throws `OSError` if unable to open `config_path`,
    `yaml.YAMLError` if the file is malformed, and
    [TODO: exception] on invalid input.

    All missing or invalid lines should be logged (also TODO)"""
    invalid_input_info = {}
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
        for backup_name, backup_spec in data.items():
            missing_specs = []
            bad_specs = {}
            if "backend" not in backup_spec or "timeframes" not in backup_spec:
                append_missing_keys(missing_specs, backup_spec, ["backend", "timeframes"])
                continue

            # Handle timeframes
            timeframes = handle_timeframes(missing_specs, bad_specs, backup_spec)

            # Handle things a bit differently based on backend.
            match backup_spec["backend"]:
                case "rsync":
                    pass
                case "btrfs":
                    pass
                case "zfs":
                    pass
                case _:
                    bad_specs["backend"] = [get_line(backup_spec), backup_spec["backend"]]

            if len(missing_specs) != 0 or len(bad_specs.keys()) != 0:
                invalid_input_info[backup_name] = [missing_specs, bad_specs]

    if len(invalid_input_info) != 0:
        # TODO: Do the logging here.
        # TODO: What to raise?
        pass


class Config:
    """Basic overview of Config class:
      config = Config(path_to_config_file)
      config.backups = [list of Backup objects]

    See tests/test_config.yaml for an example configuration file.
    """

    def __init__(self, config_path):
        pass
