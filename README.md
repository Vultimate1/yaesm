# Yaesm

Yaesm connects tools such as Btrfs, ZFS, and rsync into backup pipelines,
with optional steps such as archiving, compression, and encryption. It runs these
pipelines on demand or on a schedule, locally or over SSH.

**[Read the manual](man/yaesm.1.md)** for detailed instructions on using Yaesm.

Yaesm is pronounced "yay-zum" and does not stand for anything.

## Features

Yaesm is built around a YAML configuration file. It defines what to back up,
how to process it, where to store it, and when to run it. Each schedule can have
its own rules for keeping old backups.

- Btrfs and ZFS snapshots and incremental transfers.
- Rsync backups that share unchanged files.
- Tar archives, Zstandard compression, and GnuPG encryption.
- Copies of existing backups.
- Scheduled and on-demand backups, with groups for running related backups.
- Commands to verify your setup and find backups.
- An option to skip backups when nothing has changed.
- No backup database to maintain.

## Installation

Download pre-release packages for Debian or Fedora from the
[releases page](https://github.com/Vultimate1/yaesm/releases). Packages include a
systemd service, a manual, and Bash, Fish, and Zsh completions.

Yaesm needs Python 3.10 or newer. The [manual](man/yaesm.1.md) lists the additional
tools needed for each backup type.

## Contributing

[Issues](https://github.com/Vultimate1/yaesm/issues/new) and
[pull requests](https://github.com/Vultimate1/yaesm/pulls) are welcome.

Tests can delete files. **Run them in a virtual machine using
[`vagrant-pytest`](vagrant-pytest).**

Install [Vagrant](https://www.vagrantup.com/), [libvirt](https://libvirt.org/), and
rsync, then run these commands from the project directory:

```sh
./vagrant-pytest -H      # Show help
./vagrant-pytest tests   # Run the tests
```

See [`Vagrantfile_pytest`](Vagrantfile_pytest) for the virtual machine setup.

## AI USAGE

This project was developed in part using AI tools.

## License

[GNU GPL v3.0 or later](LICENSE)
