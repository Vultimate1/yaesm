"""The run subcommand."""

import argparse
import fcntl
import logging
import signal
from pathlib import Path

import yaesm.ty as ty
from yaesm.config import Config, ConfigError, parse_config
from yaesm.control import DEFAULT_CONTROL_SOCKET, ControlError, ControlMessage, ControlServer
from yaesm.errors import YaesmError
from yaesm.scheduler import Scheduler
from yaesm.subcommand.subcommandbase import SubcommandBase

logger = logging.getLogger(__name__)


class RunError(YaesmError):
    """Raised when the scheduler cannot be run."""


class RunSubcommand(SubcommandBase):
    """Run scheduled backups until stopped."""

    @staticmethod
    def _control_request(
        scheduler: Scheduler,
        config_path: Path,
        request: ty.Mapping[str, object],
    ) -> tuple[ControlMessage, ...]:
        command = request.get("command")
        match command:
            case "backup":
                allowed = {"command", "backup", "schedule"}
            case "reload-config":
                allowed = {"command"}
            case str() as command:
                raise ControlError(f"unknown control command: {command!r}")
            case _:
                raise ControlError("control request requires a command")

        unknown = set(request) - allowed
        if unknown:
            raise ControlError(
                f"{command} command has unknown fields: {', '.join(sorted(unknown))}"
            )
        if command == "reload-config":
            if error := RunSubcommand._reload_config(scheduler, config_path):
                raise ControlError(error)
            return ({"type": "result", "ok": True},)

        backup_name = request.get("backup")
        if not isinstance(backup_name, str) or not backup_name:
            raise ControlError("backup command requires a backup name")
        schedule_name = request.get("schedule")
        if schedule_name is not None and (not isinstance(schedule_name, str) or not schedule_name):
            raise ControlError("backup command schedule must be a nonempty string")

        request_id = scheduler.enqueue_backup(backup_name, schedule_name)
        return ({"type": "result", "ok": True, "request_id": request_id},)

    @staticmethod
    def _reload_config(scheduler: Scheduler, path: Path) -> str | None:
        try:
            config = parse_config(path)
        except ConfigError as error:
            details = "\n".join(f"  {line}" for line in error.format().splitlines())
            logger.error(
                "configuration reload failed; keeping current configuration\n%s",
                details,
            )
            return error.format()

        scheduler.replace_config(config)
        logger.info("configuration reloaded")
        return None

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
            control = ControlServer(
                arguments.control_socket,
                lambda request: self._control_request(scheduler, arguments.config, request),
            )
            signal.signal(
                signal.SIGHUP,
                lambda _signum, _frame: self._reload_config(scheduler, arguments.config),
            )
            signal.signal(signal.SIGTERM, lambda _signum, _frame: scheduler.stop())
            signal.signal(signal.SIGINT, lambda _signum, _frame: scheduler.stop())
            control.start()
            try:
                scheduler.start()
            except KeyboardInterrupt:
                pass
            finally:
                control.stop()
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
        parser.add_argument(
            "--control-socket",
            type=Path,
            default=DEFAULT_CONTROL_SOCKET,
            help="path to the control socket",
        )
