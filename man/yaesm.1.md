<!--
  yaesm(1) man page source. Written in pandoc-flavored Markdown with YAML
  frontmatter for man page metadata. The CI (build-manual.yml) converts this
  to roff via pandoc. The footer version is injected from pyproject.toml at
  build time.
-->

---
title: YAESM
section: 1
footer: yaesm VERSION
---

# NAME

yaesm - a backup tool with support for multiple file systems

# SYNOPSIS

**yaesm** [*OPTIONS*] *SUBCOMMAND* [*SUBCOMMAND-OPTIONS*]

# DESCRIPTION

**yaesm** is a backup tool that supports multiple file system backends (btrfs, rsync) and multiple backup timeframes (5-minute, hourly, daily, weekly, monthly, yearly). Backups can be performed locally or over SSH.

# OPTIONS

**\-\-version**
:   Show the program version and exit.

**-c**, **\-\-config** *FILE*
:   Path to the configuration file. Default: */etc/yaesm/config.yaml*.

**\-\-log-level** *LEVEL*
:   Set the logging level. *LEVEL* is one of **DEBUG**, **INFO**, **WARNING**, **ERROR**, **CRITICAL**. Default: **INFO**.

**\-\-log-stderr**
:   Log to standard error.

**\-\-log-file** *FILE*
:   Log to *FILE*.

**\-\-log-syslog** [*ADDRESS*]
:   Enable syslog logging and optionally specify a syslog address. Default address: */dev/log*.

# SUBCOMMANDS

**run**
:   Start the backup scheduler. The scheduler blocks indefinitely and is intended for use by init systems. See **yaesm-run**(1).

**check**
:   Validate that all preconditions for a backup are met. See **yaesm-check**(1).

**backup**
:   Perform a single manual backup. See **yaesm-backup**(1).

# CONFIGURATION

**yaesm** reads its configuration from a YAML file (default: */etc/yaesm/config.yaml*). Each top-level key defines a named backup.

## Required fields

**backend**
:   The backup backend to use. One of **btrfs** or **rsync**.

**src_dir**
:   Source directory. An absolute local path or an SSH target spec (see **SSH TARGET SPECS** below).

**dst_dir**
:   Destination directory. An absolute local path or an SSH target spec. At most one of **src_dir** and **dst_dir** may be an SSH target.

**timeframes**
:   A list of timeframe types to schedule. Valid types: **5minute**, **hourly**, **daily**, **weekly**, **monthly**, **yearly**. Each type requires additional settings (see **TIMEFRAME SETTINGS** below).

## Optional fields

**ssh_key**
:   Path to the SSH private key. Required when either **src_dir** or **dst_dir** is an SSH target.

**ssh_config**
:   Path to an SSH configuration file, passed to ssh via the **-F** flag.

**btrfs_bootstrap_refresh**
:   For the btrfs backend only. Number of days before the bootstrap snapshot is refreshed. Must be a positive integer.

## Example

```
my-home-backup:
  backend: btrfs
  src_dir: /home
  dst_dir: /mnt/backups/home
  timeframes:
    - hourly
    - daily
  hourly_keep: 24
  hourly_minutes:
    - 0
  daily_keep: 30
  daily_times:
    - 00:00
```

# TIMEFRAME SETTINGS

Each timeframe type listed in **timeframes** requires associated settings.

**5minute**
:   Requires **5minute_keep**.

**hourly**
:   Requires **hourly_keep** and **hourly_minutes**. **hourly_minutes** is a list of minutes (0-59) at which to run within each hour.

**daily**
:   Requires **daily_keep** and **daily_times**. **daily_times** is a list of times in **HH:MM** format.

**weekly**
:   Requires **weekly_keep**, **weekly_times**, and **weekly_days**. **weekly_days** is a list of day names (e.g., **monday**, **friday**).

**monthly**
:   Requires **monthly_keep**, **monthly_times**, and **monthly_days**. **monthly_days** is a list of day numbers (1-31). Months without a given day are skipped.

**yearly**
:   Requires **yearly_keep**, **yearly_times**, and **yearly_days**. **yearly_days** is a list of day-of-year numbers (1-365).

All **\*_keep** values specify the maximum number of backups to retain for that timeframe.

# SSH TARGET SPECS

An SSH target spec has the form:

> **ssh://**[**p***PORT***:**]*HOST***:***PATH*

where *HOST* is either a hostname from **~/.ssh/config** or *USER***@***HOSTNAME*, and *PATH* is an absolute path on the remote server. The **p***PORT***:** prefix is optional and defaults to port 22.

## Examples

```
ssh://p2222:fred@backupserver:/mnt/backups
ssh://backuphost:/mnt/backups
```

# EXIT STATUS

**0**
:   Success.

**1**
:   General error.

**2**
:   Subcommand-specific error (e.g., unknown backup name).

# FILES

*/etc/yaesm/config.yaml*
:   Default configuration file.

*/run/lock/yaesm-run.lock*
:   Lock file used by the **run** subcommand to prevent multiple scheduler instances.

# BUGS

Report bugs at <https://github.com/Vultimate1/yaesm/issues>.
