<!--
  yaesm(1) man page source. Written in pandoc-flavored Markdown. The CI
  (build-manual.yml) converts this to roff via pandoc, passing all man page
  metadata as CLI flags.
-->

# NAME

yaesm - a backup tool with support for multiple file systems

# SYNOPSIS

**yaesm** [*OPTIONS*] *SUBCOMMAND* [*SUBCOMMAND-OPTIONS*]

# DESCRIPTION

Yaesm creates and schedules filesystem backups. Backups may stay on the local
system, be sent to a remote system, or be pulled from a remote system. Backup
schedules, retention limits, and storage locations are defined in a YAML
configuration file.

Yaesm may be run interactively or continuously as a scheduler under an init
system.

# GLOBAL OPTIONS

Global options apply to every subcommand and must appear before the subcommand.
Running **yaesm --help** shows the global help; running a subcommand with
**--help** shows help for that subcommand.

**-h**, **--help**

Show help and exit.

**--version**

Show the installed yaesm version and exit.

**-c** *FILE*, **--config** *FILE*

Read configuration from *FILE*. The default is `/etc/yaesm/config.yaml`.

**--log-level** *LEVEL*

Set the minimum logging level. Valid values are **DEBUG**, **INFO**,
**WARNING**, **ERROR**, and **CRITICAL**. The default is **INFO**. At
**DEBUG**, every external command executed by yaesm is also logged.

**--log-stderr**

Log to standard error.

**--log-file** *FILE*

Append logs to *FILE*.

**--log-syslog**[=*ADDRESS*]

Log to syslog. *ADDRESS* is the Unix socket path and defaults to `/dev/log`.
When this option immediately precedes a subcommand, use
`--log-syslog=/dev/log` to avoid treating the subcommand as *ADDRESS*.

More than one logging destination may be selected. If none is selected, yaesm
logs to standard error.

# SUBCOMMANDS

## backup

**yaesm** [*OPTIONS*] **backup** *BACKUP*[,*BACKUP*...] [**--keep** *COUNT*]

Create one or more immediate backups. Multiple configured backup names may be
given as a comma-separated list. The names are validated before any backups
begin, then the backups run sequentially in the requested order. If one fails,
the remaining backups still run and the command returns a failure status.

Immediate backups use each selected backup's configured backend. Their
retention is managed separately from scheduled timeframes.

**--keep** *COUNT*

For each selected backup, keep no more than *COUNT* immediate backups,
including the newly created backup. *COUNT* must be a positive integer.
Immediate backups are kept indefinitely when this option is omitted.

## check

**yaesm** [*OPTIONS*] **check** [*BACKUP*[,*BACKUP*...]] [**-q** | **--quiet**]

Check whether configured backups meet the requirements needed to run. With no
backup names, all configured backups are checked. Multiple names may be given
as a comma-separated list. The names are validated before any checks begin.

By default, every check is shown as **PASS** or **FAIL**, followed by any error
messages. The command succeeds only if every selected backup passes all of its
checks. If all checks pass, the backups are very likely to run successfully,
though later runtime failures are still possible.

**-q**, **--quiet**

Show only failed checks and their error messages.

Examples:

```console
$ yaesm check
$ yaesm check home-backup
$ yaesm check home-backup,root-backup --quiet
```

## find

**yaesm** [*OPTIONS*] **find** *BACKUP*[,*BACKUP*...] [*QUERY*...] [**--query** *QUERY*...] [**--timeframe** *TIMEFRAME*[,*TIMEFRAME*...]]

Print the locations of existing backups. For each configured backup, results
are printed from newest to oldest. Multiple backup names may be given as a
comma-separated list. Each result is printed as a backup locator. Its format
depends on the backend and storage location.

With no query, all matching backups are printed. Finding no matching backups
is not an error.

The following queries are supported:

**all**

Select every backup. This is the default.

**newest**

Select the newest backup.

**oldest**

Select the oldest backup.

**after** *TIME*

Select backups newer than *TIME*.

**before** *TIME*

Select backups older than *TIME*.

**between** *TIME* *TIME*

Select backups between the two times, including the endpoints. The times
may be given in either order.

**closest** *TIME*

Select the backup closest to *TIME*.

*TIME* may have one of these forms:

- `now-Nm`, `now-Nh`, or `now-Nd` for a positive
  number of minutes, hours, or days before the current time
- **YYYY-MM-DD** for midnight on a date
- **YYYY-MM-DDTHH:MM** for a date and time
- **HH:MM** for a time today

**-q** *QUERY*, **--query** *QUERY*

Add another query. This option may be repeated. Results from multiple
queries are combined without duplicates.

**-t** *TIMEFRAME*, **--timeframe** *TIMEFRAME*, **--timeframes** *TIMEFRAME*

Limit results to one or more timeframes. Values may be comma-separated and
the option may be repeated. Valid values are **5minute**, **hourly**,
**daily**, **weekly**, **monthly**, **yearly**, and **immediate**.

Examples:

```console
$ yaesm find home-backup
$ yaesm find home-backup,root-backup newest
$ yaesm find home-backup after now-7d --timeframes daily,weekly
$ yaesm find home-backup before 2026-08-01T12:00
$ yaesm find home-backup between 2026-08-01 2026-08-15
$ yaesm find home-backup closest 12:30
$ yaesm find home-backup,root-backup --query oldest --query newest
$ yaesm find home-backup --timeframe hourly --timeframe daily
```

## run

**yaesm** [*OPTIONS*] **run** [**--lockfile** *FILE*]

Start the backup scheduler and continue running until it is stopped. Every
configured timeframe for every backup is scheduled. This command is intended
to be managed by an init system, but it may also be run directly in a terminal.

Only one scheduler may use a given lock file. If the lock cannot be acquired,
the command reports an error and exits with a failure status. **SIGINT** and
**SIGTERM** stop the scheduler gracefully and wait for running backups to
finish.

**SIGHUP** reloads the configuration file that yaesm was started with. Backups
that are already running are allowed to complete using the old configuration.
Future backups use the new configuration. If the configuration is invalid,
yaesm reports the errors and keeps the current schedule.

**--lockfile** *FILE*

Use *FILE* as the scheduler lock file. The default is
`/run/lock/yaesm-run.lock`. The file is created if needed, but its parent
directory must already exist and be writable by the running user.

# CONFIGURATION

The configuration file is the heart of yaesm. It defines the backups and
drives how they are created, scheduled, checked, found, and retained. Yaesm
reads its YAML configuration from `/etc/yaesm/config.yaml` by default. Use
**--config** to select another file. Configuration files use YAML 1.1.

The top level of the file must be a nonempty mapping defining one or more
backups. Each entry consists of a backup name, a configured backend, and a list
of timeframes. Backend-specific settings determine how and where its backups
are stored.

Yaesm provides no global backup settings or inheritance; each backup is
configured and validated independently. Unknown settings are rejected. Yaesm
reports configuration errors before running the selected subcommand.

Backup names must begin with an ASCII letter. The remaining characters may be
ASCII letters, digits, hyphens, underscores, colons, or `@` signs.

Apart from its name, every backup accepts two common settings:

**backend**

Required. The backend used to create and manage this backup. The currently
supported values are **btrfs** and **rsync**. Backend names are case-sensitive.
See [BACKENDS](#backends) for backend-specific settings, requirements,
behavior, and examples.

**timeframes**

Required. A list containing any of **5minute**, **hourly**, **daily**,
**weekly**, **monthly**, or **yearly**. Each selected timeframe requires the
corresponding settings described under [TIMEFRAMES](#timeframes). Timeframe
names are case-sensitive and each should appear only once. An empty list
creates no scheduled jobs, but the backup may still be run manually. The
**immediate** timeframe is reserved for manual backups and is not valid here.

## Timeframes

Timeframes control when scheduled backups run and how many backups are
retained. Every `*_keep` setting must be an integer greater than zero. After a
successful backup, yaesm retains at most that many backups for the same backup
and timeframe. Retention for manual backups is controlled separately by
**yaesm backup --keep**.

All schedules use the local time zone of the system running yaesm. Values in a
`*_times` list use 24-hour `HH:MM` notation. Hours range from 00 through 23 and
minutes from 00 through 59. Quote these values so YAML always treats them as
times rather than numbers. Every selected list of minutes, times, or days must
contain at least one value.

Each timeframe is scheduled independently. If two timeframes for the same
backup select the same time, both jobs may run concurrently. Choose different
times when concurrent runs are not wanted.

### 5minute

Run every five minutes.

**5minute_keep**

Required positive integer specifying how many five-minute backups to retain.

### hourly

Run every hour at each selected minute.

**hourly_keep**

Required positive integer specifying how many hourly backups to retain.

**hourly_minutes**

Required list of one or more integer minute values from 0 through 59.

### daily

Run every day at each selected time.

**daily_keep**

Required positive integer specifying how many daily backups to retain.

**daily_times**

Required list of one or more quoted `HH:MM` times.

### weekly

Run on every combination of the selected weekdays and times.

**weekly_keep**

Required positive integer specifying how many weekly backups to retain.

**weekly_times**

Required list of one or more quoted `HH:MM` times.

**weekly_days**

Required list of one or more weekday names: **monday**, **tuesday**,
**wednesday**, **thursday**, **friday**, **saturday**, or **sunday**. Names are
case-insensitive.

### monthly

Run on every combination of the selected days and times. A day that does not
exist in a particular month is skipped for that month.

**monthly_keep**

Required positive integer specifying how many monthly backups to retain.

**monthly_times**

Required list of one or more quoted `HH:MM` times.

**monthly_days**

Required list of one or more integer days from 1 through 31.

### yearly

Run on every combination of the selected days and times. Days use their
position in a non-leap year: 1 is January 1 and 365 is December 31. Day 366 is
not supported.

**yearly_keep**

Required positive integer specifying how many yearly backups to retain.

**yearly_times**

Required list of one or more quoted `HH:MM` times.

**yearly_days**

Required list of one or more integer days from 1 through 365.

# BACKENDS

A backend determines how backups are created, located, checked, and removed.
Each backend accepts its own settings and has its own system requirements. See
[CONFIGURATION](#configuration) for backup names and timeframes.

## SSH settings

Backends that connect to remote systems over SSH may share the following
settings.

**ssh_key**

Required when a backend uses an SSH target. This must be an absolute path to
an existing local private-key file used for SSH authentication.

**ssh_config**

Optional. An absolute path to an existing local OpenSSH configuration file.
When set, the file is passed to OpenSSH with **-F**.

## Path-based backends

The **btrfs** and **rsync** backends operate on local or remote directory paths.
They share the following path settings and use the SSH settings above when a
path is remote:

**src_dir**

Required. The directory to back up. This may be an absolute path to an
existing local directory or an SSH target specification.

**dst_dir**

Required. The directory in which backups are stored. This may be an absolute
path to an existing local directory or an SSH target specification. Yaesm
creates backups inside this directory.

Local paths must begin with `/` and must already exist when the configuration
is read. `~` and environment variables are not expanded.

### Remote sources and destinations

For path-based backends, an SSH target has this form:

```text
ssh://[pPORT:][USER@]HOST:/ABSOLUTE/PATH
```

The `p` before *PORT* is literal. *USER* and *PORT* are optional. *HOST* may be
a hostname or an alias from the selected OpenSSH configuration. For example:

```text
ssh://backup.example:/srv/backups
ssh://backup@backup.example:/srv/backups
ssh://p2222:backup@backup.example:/srv/backups
```

At most one of **src_dir** and **dst_dir** may be remote; remote-to-remote
backups are not supported. An explicit **ssh_key** is required even if the key
is also named in the OpenSSH configuration. Authentication must work without
interactive input, and the remote host key must already be trusted.

Yaesm rejects invalid SSH target syntax and invalid local key or configuration
files. Run **yaesm check** to test the connection, remote directory, and backend
requirements.

## btrfs

The **btrfs** backend accepts the shared path settings above. The source and
destination must be on btrfs filesystems, and the `btrfs` command must be
available wherever yaesm operates on them.

**btrfs_bootstrap_refresh**

Optional positive integer specifying the maximum age, in days, of the
bootstrap snapshot used for incremental transfers. A stale bootstrap is
recreated without removing existing backups.

## rsync

The **rsync** backend accepts the shared path settings above. The `rsync`
command must be available wherever yaesm operates on the source or destination.

**rsync_extra_opts**

Optional rsync options, given as a string or a list of strings. Each value is
split on whitespace and passed to rsync in addition to yaesm's default options.

## Examples

A local rsync backup using every configurable timeframe:

```text
home-backup:
  backend: rsync
  src_dir: /home
  dst_dir: /srv/backups
  timeframes:
    - 5minute
    - hourly
    - daily
    - weekly
    - monthly
    - yearly
  5minute_keep: 12
  hourly_keep: 24
  hourly_minutes: [1]
  daily_keep: 7
  daily_times: ["02:02"]
  weekly_keep: 4
  weekly_times: ["03:03"]
  weekly_days: [sunday]
  monthly_keep: 12
  monthly_times: ["04:04"]
  monthly_days: [1]
  yearly_keep: 5
  yearly_times: ["05:06"]
  yearly_days: [1]
```

A btrfs backup sent to a remote destination:

```text
offsite-home-backup:
  backend: btrfs
  src_dir: /home
  dst_dir: ssh://p2222:backup@backup.example:/srv/backups
  ssh_key: /root/.ssh/yaesm
  ssh_config: /root/.ssh/config
  timeframes: [daily]
  daily_keep: 7
  daily_times: ["02:00"]
```

# EXIT STATUS

**0**

The command completed successfully. This also applies when **find** has no
matches and when help or version information is requested.

**1**

The requested operation failed, a check failed, the scheduler could not run,
or yaesm encountered an unexpected error.

**2**

The command line was invalid, or **check** or **find** received an invalid
backup selection or query.

**78**

Yaesm detected a configuration error before running the subcommand.

# LICENSE

Yaesm is free software released under the GNU General Public License, version 3
or later. See the `LICENSE` file distributed with the source code for the full
license text.
