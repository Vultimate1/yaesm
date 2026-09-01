<!--
  Source for yaesm(1), written in Pandoc Markdown. The build-manual.yml CI
  workflow converts it to roff with Pandoc and supplies the man-page metadata.
-->

# NAME

yaesm - a configurable backup system that connects backup tools into pipelines

# SYNOPSIS

```text
yaesm --help
yaesm --version
yaesm [OPTIONS] SUBCOMMAND [SUBCOMMAND_OPTIONS]
```

# DESCRIPTION

Yaesm is a configurable backup system. A single [configuration file](#configuration) defines the backups it runs and how they behave.

Each backup is a pipeline assembled from components. It reads data from a source, may compress, encrypt, or otherwise transform it, and stores the result at a destination. Components may run locally or through OpenSSH, enabling local backups, remote transfers, and replication of existing backups.

Yaesm runs as a scheduler daemon, executing backups according to their schedules and accepting on-demand requests through a local control socket. A backup may have multiple schedules, each with its own retention policy. Related backups can be collected into groups and operated on together. Its [command-line subcommands](#subcommands) include operations for starting and interacting with the daemon, checking the configuration, and finding stored backups.

# CONFIGURATION

The configuration file uses YAML 1.1 and is organized around three kinds of top-level entries: one or more backup definitions, optional group definitions, and an optional `settings` section. Each backup definition describes a pipeline, each group definition collects backups and other groups under a single name, and the `settings` section controls behavior shared across backups.

## EXAMPLE CONFIGURATION

The example below shows how the pieces of a yaesm configuration fit together. It deliberately includes several optional features; a basic configuration can be much shorter, and each part is fully explained in the following sections.

```yaml
settings:
  ssh:
    identity_file: /root/.ssh/id_ed25519
    config_file: /root/.ssh/config
  scheduler:
    timezone: America/New_York
    max_concurrent_backups: 2

primary-backups:
  group:
    - encrypted-home
    - root-snapshots

encrypted-home:
  previous_names:
    - home-archive
  ssh:
    endpoint: ssh://server.example.com
  source:
    driver:
      directory: /home
    remote: true
  transforms:
    - driver: tar
      remote: true
    - driver:
        zstd: 3
      remote: true
    - driver:
        gpg: /etc/yaesm/backup-key.asc
      remote: true
  destination:
    file: /srv/backups
  schedules:
    hourly:
      previous_names:
        - frequent
      trigger:
        cron: "0 * * * *"
      retention:
        - keep-last: 24
        - keep-for: 7d
    manual:
      trigger: on-demand
      retention: keep-all

offsite-copy:
  skip_unchanged: true
  ssh:
    endpoint: ssh://backup.example.com
  source:
    backup: encrypted-home
  destination:
    driver:
      file: /srv/backups
    remote: true

root-snapshots:
  source:
    btrfs: /
  destination:
    btrfs: /.snapshots/yaesm
```

## BACKUP DEFINITIONS

Backup definitions are the core of yaesm's configuration: they determine what data is backed up, how it is processed, and where it is stored. Every top-level entry other than `settings` and group definitions is treated as a backup definition. The entry's key is the backup name, and its value is a mapping that defines the backup pipeline and its behavior.

Backup definitions share a common structure, while the `source`, `transforms`, and `destination` fields select the drivers that make up the pipeline. This section documents the common structure; see [DRIVERS](#drivers) for the settings and supported roles of each driver.

### BACKUP NAMES

The top-level key of a backup definition is its canonical name. A backup name must contain between 1 and 64 ASCII letters, digits, underscores, or hyphens; its first character cannot be a hyphen. The name `settings` is reserved regardless of capitalization.

The optional `previous_names` field is a list of earlier names for the backup. These names act as aliases, allowing existing backup artifacts and configuration references to remain associated with the backup after it is renamed. Each previous name follows the same naming rules and must be distinct from the canonical name and every other previous name.

### PIPELINE

Each backup definition requires a `source` and a `destination`. The `source` selects the driver that supplies the data, and the `destination` selects the driver that stores the resulting backup. A source may instead refer to another configured backup, allowing its artifacts to be copied through a new pipeline. Yaesm validates each pipeline while reading the configuration and rejects it if its drivers cannot exchange a compatible form of backup data.

The optional `transforms` field is a list of drivers that process the data between the source and destination, in the order listed. Omitting `transforms` defines a pipeline with no explicit transformations. A driver with no settings is specified by name; a configured driver is a mapping from its name to its value. To attach settings to the pipeline step itself, use an expanded definition whose `driver` field contains either form. See [DRIVERS](#drivers) for the available drivers and their settings.

### SSH

The optional `ssh` field configures at most one OpenSSH endpoint for a backup. To run a source, transform, or destination there, use an expanded driver definition and set its `remote` field to `true`; drivers run locally otherwise. Pipelines may therefore be local-to-local, local-to-remote, remote-to-local, or remote-to-remote on the same remote system; a pipeline cannot directly connect two different remote systems.

The `ssh` field is a mapping that requires an `endpoint` in the form `ssh://[user@]host[:port]`. The `identity_file` and `config_file` settings may also be specified here, overriding the corresponding defaults from the top-level [`settings`](#settings) section. An `identity_file` must be provided either here or in the defaults.

Yaesm supports only non-interactive OpenSSH public-key authentication; password and private-key passphrase prompts are unavailable.

### SKIP UNCHANGED

The optional `skip_unchanged` field is a boolean that defaults to `false`. When enabled and an earlier artifact exists, yaesm compares the current source with the newest artifact for the backup and skips the pipeline if they are unchanged, regardless of which schedule created that artifact. Direct pipelines require a destination driver that supports change detection; pipelines that copy an existing backup compare artifact identities. If yaesm cannot determine whether the source changed, it reports a warning and creates the backup normally.

### SCHEDULES

The optional `schedules` field is a mapping of schedule names to schedule definitions. Schedule names follow the same rules as backup names. A schedule may also have a `previous_names` list, which preserves its association with artifacts created under earlier names after the schedule is renamed.

If no on-demand schedule is configured, yaesm implicitly adds an on-demand schedule named `manual` that retains all of its artifacts. The name `manual`, regardless of capitalization, may only identify an on-demand schedule. Omitting `schedules` therefore disables timed backups but still allows the backup to be run manually.

#### SCHEDULE TYPES

Each schedule definition requires a `trigger` field that selects exactly one of the following schedule types.

- **`on-demand`**: Defines a schedule with no timer. Specify it directly as the trigger name. The schedule runs only when explicitly selected for a manual backup.
- **`cron`**: Runs the backup when a cron expression matches in the scheduler time zone. The expression contains exactly five whitespace-separated fields in this order: minute, hour, day of month, month, and day of week. Fields accept `*`, comma-separated values, ranges, and `/` steps; months and weekdays may use their three-letter English names. All fields must match. Numeric weekdays run from `0` for Monday through `6` for Sunday. Cron expressions should be quoted so YAML 1.1 treats them as strings.

#### RETENTION

Every explicitly configured schedule requires a `retention` field. Its value may be one retention policy or a nonempty list of policies. A policy with no settings is specified by name; a configured policy is a mapping from its name to its value. When multiple policies are configured, their results are combined: an artifact is retained if any policy selects it. Retention is applied after a backup artifact is created successfully.

- **`keep-all`**: Retains every artifact created by the schedule.
- **`keep-last`**: Retains the specified positive number of newest artifacts created by the schedule.
- **`keep-for`**: Retains artifacts created within the specified positive duration. A duration is an integer followed by `m`, `h`, `d`, `w`, or `y` for minutes, hours, days, weeks, or 365-day years.

## GROUP DEFINITIONS

Group definitions collect backups and other groups under one name so commands can select them together. A group is a top-level mapping with a single `group` field containing a nonempty list of backup or group names. Group names follow the backup naming rules and cannot duplicate a current or previous backup name.

A group may include other groups, and definitions may appear in any order. Expansion preserves member order and removes duplicate backups. Previous backup names may be used as members; unknown targets and group cycles are configuration errors. A group cannot be used as a backup `source`.

The implicit target `@all` selects every configured backup in definition order and cannot be redefined.

## SETTINGS

The optional `settings` section contains system-wide configuration and defaults shared by backup definitions. Its entries are organized into subsections for the parts of yaesm they configure.

### `ssh`

The optional `ssh` subsection supplies default OpenSSH client settings for backup definitions. A backup can override these defaults in its own `ssh` subsection.

- **`identity_file`**: The absolute path to the private identity file used for OpenSSH authentication. Every backup that uses OpenSSH must specify this setting either here or in its own `ssh` subsection.
- **`config_file`**: The absolute path to an OpenSSH client configuration file. When specified, yaesm passes the file to OpenSSH with `-F`.

### `scheduler`

The optional `scheduler` subsection configures how scheduled backups are run.

- **`timezone`**: The time zone used to interpret backup schedules and generate the timestamps in backup names. The value must be an IANA time zone name. The default is the system time zone.
- **`max_concurrent_backups`**: The maximum number of backups that may run concurrently. The value must be a positive integer. The default is `10`.

# DRIVERS

Drivers connect yaesm to backup tools and data formats. A driver may supply source data, transform data, store a destination artifact, or support more than one of these roles. The following sections describe each driver's supported roles, settings, and system requirements.

## directory

The `directory` driver makes an existing local or remote directory tree available as a backup source. It does not create a snapshot or store backup artifacts.

### CONFIGURATION

The `directory` value is the absolute path of the directory to read.

### PIPELINE ROLES

#### SOURCE

As a source, the `directory` driver supplies the configured directory tree to the pipeline. It is a live view rather than a point-in-time snapshot, so files may change while the pipeline reads them.

## file

The `file` driver makes an existing file available as a byte stream or stores a byte stream as a backup artifact. It may be used locally or remotely as a source or destination.

### CONFIGURATION

The `file` value is an absolute path. As a source, it names the file to read. As a destination, it names the directory beneath which artifact files are stored.

### PIPELINE ROLES

#### SOURCE

As a source, the `file` driver reads the configured file without modifying its contents. It is a live view rather than a point-in-time snapshot, so the file may change while the pipeline reads it.

#### DESTINATION

As a destination, the `file` driver writes the byte stream produced by the pipeline to a new artifact file beneath the configured directory. Filename suffixes contributed by the source and transformation drivers are preserved. Suffixes accumulate in pipeline order, so `tar` followed by `zstd` and `gpg` produces a filename ending in `.tar.zst.gpg`.

## rsync

The `rsync` driver uses the `rsync` utility to create and manage directory-tree backup artifacts. It may be used as a local or remote destination.

### CONFIGURATION

The `rsync` value is normally an absolute path. To configure additional behavior, use a mapping containing `location` and any of the optional settings below.

- **`location`**: The absolute path of the directory beneath which backup artifacts are stored.
- **`exclude`**: An `rsync` exclude pattern or list of exclude patterns. By default, no patterns are excluded.
- **`one_file_system`**: Whether `rsync` should avoid crossing filesystem boundaries while copying. The default is `false`.
- **`extra_options`**: Additional options passed to `rsync`, given as a shell-like string or list of shell-like strings. Each string is parsed using shell quoting rules. By default, no additional options are passed.

### PIPELINE ROLES

#### DESTINATION

As a destination, the `rsync` driver copies the directory tree produced by the pipeline into a new artifact directory beneath the configured directory. Copies preserve filesystem metadata, symlinks, hard links, ACLs, extended attributes, numeric ownership, and sparse files.

When an earlier compatible `rsync` artifact exists on the same system, yaesm supplies it through `--link-dest`. Rsync then hard-links unchanged files from that artifact instead of storing their data again. Every artifact remains directly browsable as a complete directory tree. The `rsync` driver does not support `skip_unchanged`.

## btrfs

The `btrfs` driver uses the `btrfs` utility to back up Btrfs subvolumes. It can provide a subvolume as a source or store read-only Btrfs snapshots as a destination.

### CONFIGURATION

The `btrfs` value is an absolute path. As a source, it names the Btrfs subvolume to back up. As a destination, it names an existing directory on a Btrfs filesystem beneath which snapshot artifacts are stored.

### PIPELINE ROLES

#### SOURCE

As a source, the `btrfs` driver takes a read-only snapshot of the configured subvolume. The pipeline may read that snapshot as a directory tree or export it as a `btrfs send` stream. Nested subvolumes are not included.

A [`file`](#file) destination may store the full send stream, appending `.btrfs` to the artifact filename. File artifacts are always full send streams, never incremental `-p` streams.

#### DESTINATION

As a destination, the `btrfs` driver stores read-only snapshot artifacts beneath the configured location. When the source is on the same system, yaesm first runs `btrfs subvolume snapshot -r`. If the source is on another system or the snapshot command exits with a failure status, yaesm falls back to piping `btrfs send` into `btrfs receive`.

When matching previous snapshots exist at both ends, the fallback uses `-p` for an incremental transfer. The resulting artifact is still a complete snapshot. A `btrfs` destination accepts only an unmodified Btrfs send stream and supports `skip_unchanged`.

## zfs

The `zfs` driver uses the `zfs` utility to back up ZFS filesystem datasets. It can provide a dataset as a source or store snapshot artifacts in a destination dataset, using `zfs send` and `zfs receive` when a transfer is required.

### CONFIGURATION

The `zfs` value is normally a ZFS dataset name. To configure native encryption, use a mapping containing `dataset` and `encryption`.

- **`dataset`**: As a source, the ZFS filesystem dataset to back up. As a destination, the dataset in which snapshot artifacts are stored.
- **`encryption`**: Whether to preserve native ZFS encryption using raw send streams. The source dataset must already be encrypted. The default is `false`.

### PIPELINE ROLES

#### SOURCE

As a source, the `zfs` driver takes a snapshot of the configured dataset. When the pipeline requires a byte stream, the snapshot is exported with `zfs send`. Child datasets are not included.

A [`file`](#file) destination may store the full send stream, appending `.zfs` to the artifact filename. File artifacts are always full send streams, never incremental `-i` streams.

#### DESTINATION

As a destination, the `zfs` driver stores snapshot artifacts in the configured dataset. When the source is the same dataset on the same system, yaesm creates the snapshot directly. Otherwise, it pipes `zfs send` into `zfs receive`.

When matching previous snapshots exist at both ends, the transfer uses `-i` to send only the changes. The resulting artifact is still a complete snapshot. A `zfs` destination accepts only an unmodified ZFS send stream and supports `skip_unchanged`.

## tar

The `tar` driver uses the `tar` utility to transform a directory tree into an uncompressed POSIX pax archive stream. It does not store the archive itself. The driver works with GNU tar or bsdtar.

### CONFIGURATION

Specify `tar` by name to use its default behavior. To override the default, its value is a mapping with the following setting:

- **`one_file_system`**: Whether `tar` should avoid crossing filesystem boundaries while reading the directory tree. The default is `true`.

### PIPELINE ROLES

#### TRANSFORM

As a transform, the `tar` driver reads the directory tree produced by the preceding driver and emits an uncompressed POSIX pax archive stream. Archives preserve filesystem metadata, ACLs, extended attributes, and numeric ownership. When the stream is stored by the [`file`](#file) driver, `.tar` is appended to the artifact filename.

## zstd

The `zstd` driver uses the `zstd` utility to compress a byte stream with Zstandard.

### CONFIGURATION

Specify `zstd` by name to use compression level `3`, or give it an integer from `1` through `19`. Higher levels generally improve compression at the cost of more time and processing.

### PIPELINE ROLES

#### TRANSFORM

As a transform, the `zstd` driver compresses the byte stream produced by the preceding driver and emits a Zstandard-compressed stream. When it is stored by the [`file`](#file) driver, `.zst` is appended to the artifact filename.

## gpg

The `gpg` driver uses the GnuPG `gpg` utility to encrypt a byte stream with an OpenPGP public key.

### CONFIGURATION

The `gpg` value is the absolute path of the OpenPGP public-key file used to encrypt the backup. The path is interpreted on the system where the transform runs, and the key does not need to be imported into a GnuPG keyring.

### PIPELINE ROLES

#### TRANSFORM

As a transform, the `gpg` driver encrypts the byte stream produced by the preceding driver and emits an OpenPGP-encrypted stream. GnuPG's internal compression is disabled; when compression is wanted, a compression transform such as [`zstd`](#zstd) should precede `gpg`. When the stream is stored by the [`file`](#file) driver, `.gpg` is appended to the artifact filename.

# GLOBAL OPTIONS

Global options must appear before the subcommand.

- **`-h`, `--help`**: Show top-level command-line help and exit. Every subcommand accepts the same option to show its own help.
- **`--version`**: Show the yaesm version and exit.
- **`-c FILE`, `--config FILE`**: Use `FILE` as the configuration file for subcommands that read it. The default is `/etc/yaesm/config.yaml`.
- **`--log-level LEVEL`**: Set the minimum logged severity to `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. The default is `INFO`.
- **`--log-syslog[=ADDRESS]`**: Send logs to the syslog socket at `ADDRESS`. When `ADDRESS` is omitted, `/dev/log` is used.
- **`--log-stderr`**: Send logs to standard error. Standard error is used automatically when no logging destination is selected.
- **`--log-file FILE`**: Append logs to `FILE`.

Multiple logging destinations may be selected together.

# SUBCOMMANDS

Where a subcommand accepts `TARGET[,TARGET...]`, each target may name a backup or group. Groups are expanded recursively, and each selected backup is operated on once. The special target `@all` selects every configured backup and cannot be combined with other targets.

## backup

### SYNOPSIS

```text
yaesm [OPTIONS] backup [--schedule SCHEDULE] [--control-socket PATH] TARGET[,TARGET...]
```

### DESCRIPTION

The `backup` subcommand asks a running yaesm scheduler to execute the selected backups immediately. It streams their log messages to standard error, waits for every selected backup to finish, and exits unsuccessfully if any backup fails. The scheduler must have been started with [`yaesm run`](#run) and must use the same control socket.

### ARGUMENTS

- **`TARGET[,TARGET...]`**: One or more backup or group names, separated by commas.

### OPTIONS

- **`--schedule SCHEDULE`**: Use the named on-demand schedule for every selected backup. If omitted, each backup must have exactly one on-demand schedule, which yaesm selects automatically.
- **`--control-socket PATH`**: Connect to the scheduler through this Unix socket. The default is `/run/yaesm/control.sock`.

## check

### SYNOPSIS

```text
yaesm [OPTIONS] check [--config-only] [-q] [TARGET[,TARGET...]]
```

### DESCRIPTION

The `check` subcommand verifies that the selected backups are ready to run. It should be used after creating or changing the configuration, before relying on the configured backups, and as the first step when investigating a backup problem. Yaesm first validates the complete configuration, then performs read-only checks to determine whether the selected backup pipelines can run.

The checks depend on the drivers in each pipeline and may cover required utilities, source and destination locations, filesystems, permissions, encryption keys, and OpenSSH installation and connectivity. Each check is reported as `PASS` or `FAIL`. The command exits successfully only when the configuration is valid and every performed check passes.

### ARGUMENTS

- **`TARGET[,TARGET...]`**: Optional backup or group names, separated by commas. The default is `@all`.

### OPTIONS

- **`--config-only`**: Stop after validating the configuration, without running driver checks.
- **`-q`, `--quiet`**: Show only failed checks.

## find

### SYNOPSIS

```text
yaesm [OPTIONS] find TARGET[,TARGET...] [QUERY...] [--query QUERY...] [--schedule SCHEDULE[,SCHEDULE...]] [-0]
```

### DESCRIPTION

The `find` subcommand lists existing backup artifacts for the selected backups. Each result is written to standard output as a destination-specific locator. Results are ordered newest to oldest within each backup. Finding no matching artifacts is successful and produces no output.

### ARGUMENTS

- **`TARGET[,TARGET...]`**: One or more backup or group names, separated by commas.
- **`QUERY...`**: An optional query selecting artifacts by creation time. Omitting the query is equivalent to `all`.

### QUERIES

Queries are applied separately to each selected backup and use one of the following forms:

- **`all`**: Select every artifact.
- **`newest`**: Select the newest artifact.
- **`oldest`**: Select the oldest artifact.
- **`after TIME`**: Select artifacts created strictly after `TIME`.
- **`before TIME`**: Select artifacts created strictly before `TIME`.
- **`between TIME TIME`**: Select artifacts created at or between the two times. The times may be given in either order.
- **`closest TIME`**: Select the artifact whose creation time is closest to `TIME`. If two artifacts are equally close, the newer one is selected.

### TIME VALUES

`TIME` may be a date in `YYYY-MM-DD` form, a date and time in `YYYY-MM-DDTHH:MM` form, or a time on the current date in `HH:MM` form. A date without a time means midnight. Date-and-time values may include `Z` or an ISO 8601 UTC offset; values without an offset are interpreted in the configured scheduler time zone. Ambiguous local times require an explicit offset, and nonexistent local times are rejected.

A relative time has the form `now-Nm`, `now-Nh`, or `now-Nd`, where `N` is a positive decimal integer without leading zeros and the suffix selects elapsed minutes, hours, or days.

### OPTIONS

- **`-q QUERY...`, `--query QUERY...`**: Add another query. This option may be repeated; an artifact selected by any query is shown once.
- **`-s SCHEDULE[,SCHEDULE...]`, `--schedule SCHEDULE[,SCHEDULE...]`, `--schedules SCHEDULE[,SCHEDULE...]`**: Limit results to artifacts created by the named schedules. Current or previous schedule names may be used, names may be comma-separated, and the option may be repeated.
- **`-0`**: Terminate each result with a NUL byte instead of a newline.

## run

### SYNOPSIS

```text
yaesm [OPTIONS] run [--lockfile FILE] [--control-socket PATH]
```

### DESCRIPTION

The `run` subcommand starts the yaesm scheduler in the foreground and runs configured timed backups until stopped. It is generally launched and supervised by an init system. It also creates the control socket used by [`yaesm backup`](#backup) to request immediate backups. An exclusive lock prevents more than one scheduler using the same lock file.

### SCHEDULING BEHAVIOR

Executions of the same backup are serialized. An immediate execution, or an occurrence from another schedule for that backup, waits for the current execution to finish. If another timed occurrence of the same scheduled job becomes due while its previous occurrence is still running, the new occurrence is skipped and a warning is logged. Different backups may run concurrently, subject to the configured `max_concurrent_backups` limit.

### OPTIONS

- **`--lockfile FILE`**: Acquire the scheduler lock through `FILE`. The default is `/run/lock/yaesm-run.lock`.
- **`--control-socket PATH`**: Listen for control requests on this Unix socket. The default is `/run/yaesm/control.sock`; the socket is created with mode `0600`.

### SIGNALS

- **`SIGHUP`**: Reload the configuration file selected by `--config`. A valid configuration becomes active for future scheduled and requested backups without interrupting running backups. If validation fails, the error is logged and the current configuration remains active.
- **`SIGINT`, `SIGTERM`**: The first signal begins a graceful shutdown. Yaesm stops accepting backups and configuration reloads, lets running backups finish, then closes the control socket and exits. A second signal forcibly terminates running backup commands and causes yaesm to exit unsuccessfully; incomplete temporary or pending artifacts may remain.

An init system should enforce a final stop timeout and forcibly terminate yaesm and its remaining child processes if graceful shutdown exceeds it.

# FILES

- **`/etc/yaesm/config.yaml`**: Default configuration file.
- **`/run/lock/yaesm-run.lock`**: Default scheduler lock file.
- **`/run/yaesm/control.sock`**: Default Unix socket used for control requests between `backup` and `run`.

# EXIT STATUS

- **`0`**: The requested operation completed successfully.
- **`1`**: The requested operation failed, a check or backup failed, yaesm encountered an unexpected error, or scheduler shutdown was forced.
- **`2`**: The command line was invalid.
- **`78`**: The configuration could not be read or validated.

# SEE ALSO

`ssh(1)`, `ssh_config(5)`, `rsync(1)`, `tar(1)`, `btrfs(8)`, `btrfs-send(8)`, `btrfs-receive(8)`, `zfs(8)`, `zfs-send(8)`, `zfs-receive(8)`, `zstd(1)`, `gpg(1)`

# AUTHORS

Yaesm was developed by Connor Gallivan and Nicholas B. Hubbard through the University of Massachusetts Lowell Open Source Club.

# LICENSE

Yaesm is free software released under the GNU General Public License, version 3 or later. See the `LICENSE` file distributed with yaesm for the full license text.
