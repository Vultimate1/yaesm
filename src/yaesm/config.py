import yaml

class Config:
    """Basic overview of Config class:
      config = Config(path_to_config_file)
      config.backups = [list of Backup objects]

    Example configuration file (yaml):
    ---
    # This is an example yaesm configuration file

    root_backup:
      backend: rsync
      src_dir: /
      dst_dir: /mnt/backupdrive/yaesm/root_backup

      # This backup uses every timeframe category
      timeframes: [5minute, hourly, daily, weekly, monthly, yearly]

      5minute_keep: 24

      hourly_minutes: [0, 30]
      hourly_keep: 48

      daily_times: [23:59, 11:59]
      daily_keep: 720 # can go back an entire year!

      weekly_keep: 21
      weekly_days: [monday, friday, sunday]
      weekly_times: [23:59, 05:30]

      monthly_keep: 100
      monthly_days: [1, 7, 30]
      monthly_times: [23:59]

      yearly_keep: 5
      yearly_days: [1, 129]
      yearly_times: [23:59]

    home_backup:
      backend: btrfs
      src_dir: /home/fred
      dst_dir: fred@192.168.1.73:/yaesm/fred_laptop_backups # backup to a remote server with SSH!
      ssh_key: /home/fred/.ssh/id_rsa
      ssh_config: /etc/ssh/some_other_config
      timeframes: [daily]
      daily_times: [23:59]
      daily_keep: 365

    database_snapshot:
      backend: zfs
      src_dir: /important-database
      dst_dir: /.snapshots/yaesm/important-database
      timeframes: [hourly]
      hourly_keep: 10000000000 # forever ... until we run out of disk space
      hourly_minutes: [0]
    """

    def __init__(self, config_path):
        ...
