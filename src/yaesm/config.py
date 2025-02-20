import yaml

def parse_file(config_path):
    """Returns a list of backups, given a valid YAML config file.

    Throws `OSError` if unable to open `config_path`, and
    [TODO: exception] on invalid input.
    All missing or invalid lines should be logged (also TODO)"""
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
        print(data)


class Config:
    """Basic overview of Config class:
      config = Config(path_to_config_file)
      config.backups = [list of Backup objects]

    See tests/test_config.yaml for an example configuration file.
    """

    def __init__(self, config_path):
        pass
