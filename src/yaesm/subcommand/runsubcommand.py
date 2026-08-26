import argparse
import fcntl
import logging
import os
import signal
from pathlib import Path

import yaesm.config
import yaesm.scheduler
from yaesm.backup import Backup
from yaesm.cleanup import Cleanup
from yaesm.subcommand.subcommandbase import SubcommandBase

logger = logging.getLogger(__name__)


class RunSubcommand(SubcommandBase):
    """Start the backup scheduler (blocks indefinitely; intended for use by init systems)."""

    @staticmethod
    def _reload_config(scheduler: yaesm.scheduler.Scheduler, config_file: Path) -> None:
        try:
            backups = yaesm.config.parse_config(config_file)
        except yaesm.config.ConfigErrors as exc:
            error_count = len(exc.errors)
            logger.error(
                "configuration reload failed with %d error%s; keeping existing schedule",
                error_count,
                "" if error_count == 1 else "s",
            )
            for backup, error in exc.errors:
                logger.error("    %s: %s", backup, error)
            return

        scheduler.replace_backups(backups)
        logger.info("configuration reloaded")

    def main(self, backups: list[Backup], parsed_args: argparse.Namespace) -> int:
        try:
            lock_fd = os.open(parsed_args.lockfile, os.O_WRONLY | os.O_CREAT, 0o644)
            fcntl.lockf(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd = lock_fd
        except OSError as e:
            logger.error(f"could not acquire scheduler lock: {parsed_args.lockfile}: {e}")
            return 1

        scheduler = yaesm.scheduler.Scheduler()
        scheduler.add_backups(backups)
        Cleanup.add_function(lambda s=scheduler: s.stop())
        signal.signal(
            signal.SIGHUP,
            lambda _signum, _frame: self._reload_config(scheduler, parsed_args.config),
        )

        try:
            scheduler.start()  # blocks
        except (KeyboardInterrupt, SystemExit):
            logger.info("scheduler stopped gracefully")
            return 0
        except Exception:
            logger.error("scheduler crashed", exc_info=True)
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
