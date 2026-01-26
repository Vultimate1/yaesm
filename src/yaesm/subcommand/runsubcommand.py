import os
import fcntl
import argparse
from pathlib import Path

from yaesm.subcommand.subcommandbase import SubcommandBase
from yaesm.logging import logger
import yaesm.cleanup
import yaesm.scheduler

class RunSubcommand(SubcommandBase):
    """The run subcommand runs the scheduler, which blocks, and in most cases never terminates.

    This subcommand should primarily be invoked from OS init system software.
    """
    def main(self, backups, parsed_args) -> int:
        try:
            lock_fd = os.open(parsed_args.lockfile, os.O_WRONLY | os.O_CREAT, 0o644)
            fcntl.lockf(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd = lock_fd
        except OSError:
            logger().error(f"could not acquire scheduler lock: {parsed_args.lockfile}")
            return 1

        scheduler = yaesm.scheduler.Scheduler()
        scheduler.add_backups(backups)
        yaesm.cleanup.add_cleanup_function(lambda s=scheduler: s.stop())

        try:
            scheduler.start() # blocks
            return 0
        except (KeyboardInterrupt, SystemExit):
            logger().info("scheduler stopped gracefully")
            return 0
        except Exception:
            logger().error("scheduler crashed", exc_info=True)
            return 1

    @classmethod
    def add_argparser_arguments(cls, parser:argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--lockfile",
            type=Path,
            default=Path("/run/lock/yaesm-run.lock"),
            help="path to lock file"
        )
