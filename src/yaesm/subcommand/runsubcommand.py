"""The run subcommand."""

import argparse
import fcntl
import logging
import os
import signal
from pathlib import Path
from threading import Thread

import yaesm.ty as ty
from yaesm.command import cancel_commands
from yaesm.config import Config, ConfigError, parse_config
from yaesm.control import DEFAULT_CONTROL_SOCKET, ControlError, ControlMessage, ControlServer
from yaesm.errors import YaesmError
from yaesm.scheduler import Scheduler, SchedulerError
from yaesm.subcommand.subcommandbase import (
    SubcommandBase,
    TargetSelection,
    TargetSelectionMode,
)

logger = logging.getLogger(__name__)


def _force_shutdown() -> ty.NoReturn:
    try:
        cancel_commands()
    finally:
        os._exit(1)


class RunError(YaesmError):
    """Raised when the scheduler cannot be run."""


class RunSubcommand(SubcommandBase):
    """Run scheduled backups until stopped."""

    target_selection = TargetSelectionMode.NONE

    @staticmethod
    def _control_request(
        scheduler: Scheduler,
        config_path: Path,
        request: ty.Mapping[str, object],
    ) -> ty.Iterable[ControlMessage]:
        command = request.get("command")
        match command:
            case "backup":
                allowed = {"command", "targets", "schedule"}
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
            result: ControlMessage = {"type": "result", "ok": True, "request_id": None}
            return (result,)

        target_names = request.get("targets")
        if (
            not isinstance(target_names, list | tuple)
            or not target_names
            or any(not isinstance(name, str) or not name for name in target_names)
        ):
            raise ControlError("backup command requires backup targets")
        try:
            targets = TargetSelection(tuple(dict.fromkeys(target_names)))
        except ValueError as error:
            raise ControlError(str(error)) from error
        schedule_name = request.get("schedule")
        if schedule_name is not None and (not isinstance(schedule_name, str) or not schedule_name):
            raise ControlError("backup command schedule must be a nonempty string")

        request_id = scheduler.enqueue_targets(targets.names, schedule_name)
        return scheduler.request_messages(request_id)

    @staticmethod
    def _reload_config(scheduler: Scheduler, path: Path) -> str | None:
        try:
            config = parse_config(path)
            scheduler.replace_config(config)
        except (ConfigError, SchedulerError) as error:
            details = "\n".join(f"  {line}" for line in error.format().splitlines())
            logger.error(
                "configuration reload failed; keeping current configuration\n%s",
                details,
            )
            return error.format()

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
            stopping = False
            forced = False

            def stop(_signum: int, _frame: object) -> None:
                nonlocal stopping, forced
                if forced:
                    return
                if stopping:
                    forced = True
                    logger.warning("forced shutdown requested; terminating running backups")
                    target = _force_shutdown
                    name = "yaesm-force-shutdown"
                else:
                    stopping = True
                    logger.info("graceful shutdown requested; waiting for running backups")
                    target = scheduler.stop
                    name = "yaesm-shutdown"
                Thread(target=target, name=name).start()

            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            control.start()
            try:
                scheduler.start()
            except KeyboardInterrupt:
                pass
            finally:
                control.stop()
                scheduler.stop()

        logger.info("scheduler stopped")
        return int(forced)

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
