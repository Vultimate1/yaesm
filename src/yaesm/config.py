import yaml

class Config:

    """
    Basic overview of Config class:
      config = Config.new(path_to_config_file)
      config.snapshots = [list of Snapshot objects]
      config.local_backups = [list of LocalBackup objects]
      config.ssh_backups = [list of SSHBackup objects]
      config.settings.snapshot_directory = path_to_snapshot_directory

    Example configuration file (yaml):
      ---
      snapshot_directory: /.snapshots

      snapshots:
        root_snapshot:
          directory: /
          retention_policy: timeframes
          timeframes:
            5minute
            hourly
            daily
          5minute_keep: 24
          hourly_keep: 24
          daily_keep: 30
          daily_times:
            11:30
            23:59

        home_snapshot:
          directory: /home
          retention_policy: timeframes
          timeframes:
            daily
          daily_keep: 30
          daily_times:
            11:30
            23:59


      ssh_backups:
        home_to_my_server:
          directory: /home
          ssh_dest: larry@192.168.1.73:/backups/yaesm
          ssh_key: /home/user/.ssh/id_ed25519
          retention_policy: timeframes
          timeframes:
            daily
          daily_keep: 365
          daily_times:
            23:59

      local_backups:
        home_to_drive:
          directory: /home
          retention_policy: timeframes
          timeframes:
            hourly
          hourly_keep:
            36
    """

    def __init__(self, config_path):
        with open(config_path, "r") as file:
            c = yaml.safe_load(file)

        self.snapshots = []
        for name, vals in c["snapshots"].items():
            snapshot = Snapshot.new(name, vals)
            self.snapshots.append(snapshot)

        self.local_backups = []
        for name, vals in c["local_backups"].items():
            local_backup = LocalBackup.new(name, vals)
            self.local_backups.append(local_backup)

        self.ssh_backups = []
        for name, vals in c["ssh_backups"].items():
            ssh_backup = SSHBackup.new(name, vals)
            self.ssh_backups.append(ssh_backup)

        for key, val in c["settings"]:
            if self.__valid_setting(name, val):
                self.settings.key = val
