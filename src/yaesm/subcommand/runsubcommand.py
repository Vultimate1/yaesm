import os
import argparse
from pathlib import Path

from yaesm.subcommand.subcommandbase import SubcommandBase
from yaesm.logging import logger
import yaesm.cleanup
import yaesm.scheduler

class RunSubcommand(SubcommandBase):
    """TODO"""
    def main(self, backups, parsed_args) -> int:
        """TODO"""
        self._setup_pidfile(parsed_args.pidfile)

        scheduler = yaesm.scheduler.Scheduler()
        scheduler.add_backups(backups)
        yaesm.cleanup.add_cleanup_function(lambda s=scheduler: s.stop())
        scheduler.start() # blocks

        return 0

    @classmethod
    def add_argparser_arguments(cls, parser:argparse.ArgumentParser) -> None:
        """TODO"""
        parser.add_argument(
            "--pidfile",
            type=Path,
            default=Path("/var/run/yaesm.pid"),
            help="path to PID file"
        )

    def _setup_pidfile(self, pidfile:Path) -> bool:
        """Create and register cleanup for pidfile.

        Returns:
            True if pidfile was successfully created, False if daemon is already running.
        """
        if pidfile.is_file():
            with open(pidfile, "r") as fr:
                try:
                    existing_pid = int(fr.read().strip())
                    os.kill(existing_pid, 0)
                    return False
                except (ValueError, ProcessLookupError, OSError):
                    logger().warning(f"removing stale pidfile: {pidfile}")
                    pidfile.unlink(missing_ok=True)
        else:
            with open(pidfile, "w") as fw:
                fw.write(f"{os.getpid()}\n")
            yaesm.cleanup.add_cleanup_function(lambda pf=pidfile: pf.unlink(missing_ok=True))
            return True
