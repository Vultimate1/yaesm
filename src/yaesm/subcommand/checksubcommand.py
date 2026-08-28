"""The check subcommand."""

import argparse

import yaesm.ty as ty
from yaesm.backup import Backup, DriverSource
from yaesm.check import Check, CheckResult, CheckRole
from yaesm.config import Config
from yaesm.errors import YaesmError
from yaesm.ssh import SSHTarget
from yaesm.subcommand.subcommandbase import SubcommandBase


class CheckError(YaesmError):
    """Raised when configured backups cannot be checked."""


class CheckSubcommand(SubcommandBase):
    """Check whether configured backups can run."""

    def main(self, config: Config, arguments: argparse.Namespace) -> int:
        backups = self._select_backups(config, arguments.backup_names)
        openssh_result = None
        connection_results: dict[SSHTarget, CheckResult] = {}
        passed = True

        for backup in backups:
            if not arguments.quiet:
                print(f"backup: {backup.name}", flush=True)
            checks = self._backup_checks(backup, config.backups)
            targets = tuple(
                dict.fromkeys(check.target for check in checks if check.target is not None)
            )
            results = []

            if targets:
                if openssh_result is None:
                    openssh_result = Check.command("OpenSSH is installed", ("ssh", "-V")).run()
                results.append(openssh_result)
                if openssh_result.passed:
                    for target in targets:
                        if target not in connection_results:
                            connection_results[target] = Check.command(
                                "SSH connection works",
                                ("true",),
                                target=target,
                                failure_message=f"could not connect to {target}",
                            ).run()
                        results.append(connection_results[target])

            for check in checks:
                if check.target is None or (
                    openssh_result is not None
                    and openssh_result.passed
                    and connection_results[check.target].passed
                ):
                    results.append(check.run())

            failures = tuple(result for result in results if not result.passed)
            if failures:
                passed = False
            if arguments.quiet and failures:
                print(f"backup: {backup.name}")
            for result in failures if arguments.quiet else results:
                self._print_result(result)

        return 0 if passed else 1

    @staticmethod
    def _select_backups(config: Config, names: tuple[str, ...] | None) -> tuple[Backup, ...]:
        if names is None:
            return tuple(config.backups.values())
        if not names:
            raise CheckError("no backup names specified")
        if unknown := tuple(name for name in names if name not in config.backups):
            label = "backup" if len(unknown) == 1 else "backups"
            raise CheckError(f"unknown {label}: {', '.join(repr(name) for name in unknown)}")
        return tuple(config.backups[name] for name in names)

    @staticmethod
    def _backup_checks(
        backup: Backup,
        backups: ty.Mapping[str, Backup],
    ) -> tuple[Check, ...]:
        if isinstance(backup.source, DriverSource):
            source = backup.source.driver
            source_checks = source.check(CheckRole.SOURCE)
        else:
            source_backup = backups[backup.source.backup_name]
            source = source_backup.destination
            source_checks = (
                *source.check(CheckRole.ARTIFACT_SOURCE),
                source.check_artifacts(source_backup.name),
            )
        return (
            *source_checks,
            *(check for driver in backup.drivers for check in driver.check(CheckRole.TRANSFORM)),
            *backup.destination.check(CheckRole.DESTINATION),
        )

    @staticmethod
    def _print_result(result: CheckResult) -> None:
        status = "PASS" if result.passed else "FAIL"
        print(f"    {status}  {result.description}")
        if result.passed:
            return
        details = dict.fromkeys(
            line
            for detail in (result.failure, result.stdout, result.stderr)
            if detail
            for line in detail.rstrip().splitlines()
        )
        for line in details:
            print(f"          {line}")

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "backup_names",
            nargs="?",
            metavar="BACKUP[,BACKUP...]",
            type=lambda value: tuple(dict.fromkeys(filter(None, map(str.strip, value.split(","))))),
            help="names of backups to check (default: all)",
        )
        parser.add_argument(
            "-q",
            "--quiet",
            action="store_true",
            help="show only failed checks",
        )
