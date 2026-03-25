import argparse
import fcntl
import os
from pathlib import Path

import yaesm.scheduler
from yaesm.backup import Backup
from yaesm.cleanup import Cleanup
from yaesm.logging import Logging
from yaesm.subcommand.subcommandbase import SubcommandBase


class RunSubcommand(SubcommandBase):
    """Start the backup scheduler (blocks indefinitely; intended for use by init systems)."""

    def main(self, backups: list[Backup], parsed_args: argparse.Namespace) -> int:
        try:
            lock_fd = os.open(parsed_args.lockfile, os.O_WRONLY | os.O_CREAT, 0o644)
            fcntl.lockf(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd = lock_fd
        except OSError as e:
            Logging.get().error(f"could not acquire scheduler lock: {parsed_args.lockfile}: {e}")
            return 1

        scheduler = yaesm.scheduler.Scheduler()
        scheduler.add_backups(backups)
        Cleanup.add_function(lambda s=scheduler: s.stop())

        try:
            scheduler.start()  # blocks
        except (KeyboardInterrupt, SystemExit):
            Logging.get().info("scheduler stopped gracefully")
            return 0
        except Exception:
            Logging.get().error("scheduler crashed", exc_info=True)
            return 1

        return 0

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--lockfile",
            type=Path,
            default=Path("/run/lock/yaesm-run.lock"),
            help="path to lock file",
        )
