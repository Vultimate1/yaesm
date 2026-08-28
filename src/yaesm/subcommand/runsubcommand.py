"""The run subcommand."""

import argparse
import fcntl
import logging
import signal
from pathlib import Path

from yaesm.config import Config, ConfigError, parse_config
from yaesm.errors import YaesmError
from yaesm.scheduler import Scheduler
from yaesm.subcommand.subcommandbase import SubcommandBase

logger = logging.getLogger(__name__)


class RunError(YaesmError):
    """Raised when the scheduler cannot be run."""


class RunSubcommand(SubcommandBase):
    """Run scheduled backups until stopped."""

    @staticmethod
    def _reload_config(scheduler: Scheduler, path: Path) -> None:
        try:
            config = parse_config(path)
        except ConfigError as error:
            details = "\n".join(f"  {line}" for line in error.format().splitlines())
            logger.error(
                "configuration reload failed; keeping current configuration\n%s",
                details,
            )
            return

        scheduler.replace_config(config)
        logger.info("configuration reloaded")

    def main(self, config: Config, arguments: argparse.Namespace) -> int:
        try:
            lock_file = arguments.lockfile.open("a")
        except OSError as error:
            raise RunError(
                f"could not open scheduler lock {arguments.lockfile}: {error}"
            ) from error
        try:
            fcntl.lockf(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            lock_file.close()
            raise RunError(
                f"could not acquire scheduler lock {arguments.lockfile}: {error}"
            ) from error

        with lock_file:
            scheduler = Scheduler(config)
            signal.signal(
                signal.SIGHUP,
                lambda _signum, _frame: self._reload_config(scheduler, arguments.config),
            )
            signal.signal(signal.SIGTERM, lambda _signum, _frame: scheduler.stop())
            signal.signal(signal.SIGINT, lambda _signum, _frame: scheduler.stop())
            try:
                scheduler.start()
            except KeyboardInterrupt:
                pass
            finally:
                scheduler.stop()

        logger.info("scheduler stopped")
        return 0

    @classmethod
    def add_argparser_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--lockfile",
            type=Path,
            default=Path("/run/lock/yaesm-run.lock"),
            help="path to the scheduler lock file",
        )
