"""Scheduling of configured backups."""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import queue
import time
from _thread import LockType
from datetime import datetime, timezone, tzinfo
from threading import Lock
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import voluptuous as vlp
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.base import BaseTrigger
from tzlocal import get_localzone

import yaesm.ty as ty
from yaesm.backup import Backup
from yaesm.control import ControlFailure, ControlMessage
from yaesm.errors import YaesmError, YaesmValueError
from yaesm.logging import RequestFilter, current_backup, format_duration, request_id
from yaesm.schedule import OnDemandSchedule

if ty.TYPE_CHECKING:
    from yaesm.config import Config

logger = logging.getLogger(__name__)
_backend_logger = logging.getLogger(f"{__name__}.backend")


def _positive_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise vlp.Invalid("must be a positive integer")
    return value


def _timezone(value: object) -> ZoneInfo:
    if not isinstance(value, str):
        raise vlp.Invalid("must be a timezone name")
    try:
        return ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise vlp.Invalid(f"unknown timezone: {value!r}") from error


_GLOBAL_SETTINGS_SCHEMA = vlp.Schema(
    {
        vlp.Optional("max_concurrent_backups"): _positive_integer,
        vlp.Optional("timezone"): _timezone,
    }
)


class SchedulerError(YaesmError):
    """Raised when a backup cannot be scheduled."""


@dataclasses.dataclass(frozen=True)
class _RequestState:
    messages: queue.Queue[ControlMessage]
    result_count: int


class Scheduler:
    """Schedule and run configured backup jobs."""

    global_settings_key: ty.ClassVar[str] = "scheduler"
    global_settings_schema: ty.ClassVar[vlp.Schema] = _GLOBAL_SETTINGS_SCHEMA

    @classmethod
    def timezone(cls, config: Config) -> tzinfo:
        """Return the configured timezone or the system timezone."""
        settings = config.global_settings.get(cls.global_settings_key, {})
        assert isinstance(settings, dict)
        timezone = settings.get("timezone")
        if timezone is None:
            timezone = get_localzone()
        assert isinstance(timezone, tzinfo)
        return timezone

    def __init__(self, config: Config) -> None:
        self._lock = Lock()
        self._backup_locks: dict[str, LockType] = {}
        self._requests: dict[UUID, _RequestState] = {}
        self._timer_job_ids: set[str] = set()
        settings = config.global_settings.get(self.global_settings_key, {})
        assert isinstance(settings, dict)
        max_workers = settings.get("max_concurrent_backups", 10)
        assert isinstance(max_workers, int)
        self._scheduler = BlockingScheduler(
            executors={"default": ThreadPoolExecutor(max_workers=max_workers)},
            job_defaults={"max_instances": 1},
            logger=_backend_logger,
        )
        self.replace_config(config)

    def replace_config(self, config: Config) -> None:
        """Replace scheduled jobs without interrupting running jobs."""
        with self._lock:
            self._config = config
            for job_id in self._timer_job_ids:
                if self._scheduler.get_job(job_id) is not None:
                    self._scheduler.remove_job(job_id)
            self._timer_job_ids.clear()
            timezone = self.timezone(config)
            for backup in config.backups.values():
                for schedule in backup.schedules:
                    for index, trigger in enumerate(schedule.timer_triggers(timezone)):
                        job_id = f"{backup.name}:{schedule.name}:{index}"
                        self._add_job(
                            backup,
                            schedule.name,
                            config.backups_by_name,
                            timezone,
                            trigger=trigger,
                            job_id=job_id,
                        )
                        self._timer_job_ids.add(job_id)

    def enqueue_backup(self, backup_name: str, schedule_name: str | None = None) -> UUID:
        """Queue a configured backup for immediate execution."""
        return self.enqueue_targets((backup_name,), schedule_name)

    def enqueue_targets(
        self,
        target_names: ty.Sequence[str],
        schedule_name: str | None = None,
    ) -> UUID:
        """Queue the backups represented by targets for immediate execution."""
        with self._lock:
            config = self._config
            if not target_names:
                raise SchedulerError("no backup targets specified")
            try:
                backups = config.backups_for_targets(*target_names)
            except YaesmValueError as error:
                raise SchedulerError(str(error)) from None
            scheduled = tuple(
                (backup, self._on_demand_schedule(backup, schedule_name)) for backup in backups
            )

            request_id = uuid4()
            messages: queue.Queue[ControlMessage] = queue.Queue()
            self._requests[request_id] = _RequestState(messages, len(scheduled))
            job_ids = []
            try:
                for index, (backup, selected_schedule_name) in enumerate(scheduled):
                    with _stream_request_logs(request_id, messages):
                        logger.info("backup %r (%s) queued", backup.name, selected_schedule_name)
                    job_id = str(request_id) if len(scheduled) == 1 else f"{request_id}:{index}"
                    self._add_job(
                        backup,
                        selected_schedule_name,
                        config.backups_by_name,
                        self.timezone(config),
                        request_id=request_id,
                        job_id=job_id,
                    )
                    job_ids.append(job_id)
            except BaseException:
                for job_id in job_ids:
                    if self._scheduler.get_job(job_id) is not None:
                        self._scheduler.remove_job(job_id)
                del self._requests[request_id]
                raise
        return request_id

    @staticmethod
    def _on_demand_schedule(backup: Backup, schedule_name: str | None) -> str:
        schedules = [
            schedule
            for schedule in backup.schedules
            if isinstance(schedule.implementation, OnDemandSchedule)
        ]
        if schedule_name is None:
            if not schedules:
                raise SchedulerError(f"backup {backup.name!r} has no on-demand schedule")
            if len(schedules) > 1:
                raise SchedulerError(f"backup {backup.name!r} has multiple on-demand schedules")
            return schedules[0].name

        schedule = next(
            (schedule for schedule in backup.schedules if schedule_name in schedule.names),
            None,
        )
        if schedule is None:
            raise SchedulerError(f"backup {backup.name!r} has no schedule {schedule_name!r}")
        if schedule not in schedules:
            raise SchedulerError(
                f"schedule {schedule_name!r} for backup {backup.name!r} is not on-demand"
            )
        return schedule.name

    def request_messages(self, request_id: UUID) -> ty.Iterator[ControlMessage]:
        """Yield a queued backup's logs followed by its result."""
        with self._lock:
            try:
                request = self._requests[request_id]
            except KeyError as error:
                raise SchedulerError(f"unknown backup request: {request_id!r}") from error

        failures: list[ControlFailure] = []
        results = 0
        try:
            while results < request.result_count:
                message = request.messages.get()
                if message.get("type") == "log":
                    yield message
                    continue
                results += 1
                if message.get("ok") is not True:
                    failures.append(ty.cast(ControlFailure, message))

            serialized_request_id = str(request_id)
            if not failures:
                yield {
                    "type": "result",
                    "ok": True,
                    "request_id": serialized_request_id,
                }
            elif len(failures) == 1:
                yield failures[0]
            else:
                errors = tuple(dict.fromkeys(failure["error"] for failure in failures))
                yield {
                    "type": "result",
                    "ok": False,
                    "error": "multiple backups failed:\n"
                    + "\n".join(f"  - {error}" for error in errors),
                    "error_logged": all(failure["error_logged"] for failure in failures),
                    "request_id": serialized_request_id,
                }
        finally:
            with self._lock:
                self._requests.pop(request_id, None)

    def _add_job(
        self,
        backup: Backup,
        schedule_name: str,
        backups: ty.Mapping[str, Backup],
        timezone: tzinfo,
        *,
        job_id: str,
        trigger: BaseTrigger | None = None,
        request_id: UUID | None = None,
    ) -> None:
        backup_lock = self._backup_locks.setdefault(backup.name, Lock())
        messages = None if request_id is None else self._requests[request_id].messages
        self._scheduler.add_job(
            _execute_backup,
            trigger=trigger,
            args=(backup, schedule_name, backups, request_id, messages, timezone, backup_lock),
            id=job_id,
            name=f"{backup.name} ({schedule_name})",
        )

    def start(self) -> None:
        """Start the blocking scheduler."""
        if not self._scheduler.running:
            self._scheduler.start()

    def stop(self) -> None:
        """Stop accepting jobs without waiting for running jobs."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)


def _execute_backup(
    backup: Backup,
    schedule_name: str,
    backups: ty.Mapping[str, Backup],
    backup_request_id: UUID | None,
    messages: queue.Queue[ControlMessage] | None,
    scheduler_timezone: tzinfo,
    backup_lock: LockType,
) -> None:
    serialized_request_id = None if backup_request_id is None else str(backup_request_id)
    with _stream_request_logs(backup_request_id, messages):
        backup_token = current_backup.set(f"backup {backup.name!r} ({schedule_name})")
        started = None
        try:
            if not backup_lock.acquire(blocking=False):
                logger.info(
                    "backup %r (%s) waiting for another execution",
                    backup.name,
                    schedule_name,
                )
                backup_lock.acquire()
            try:
                logger.info("backup %r (%s) started", backup.name, schedule_name)
                started = time.monotonic()
                artifact = backup.execute(
                    schedule_name,
                    datetime.now(timezone.utc).astimezone(scheduler_timezone),
                    backups,
                )
                logger.info(
                    "backup %r (%s) completed in %s: %s",
                    backup.name,
                    schedule_name,
                    format_duration(time.monotonic() - started),
                    backup.destination.format_locator(artifact),
                )
            finally:
                backup_lock.release()
        except BaseException as error:
            elapsed = (
                "" if started is None else f" after {format_duration(time.monotonic() - started)}"
            )
            if isinstance(error, YaesmError):
                logger.error(
                    "backup %r (%s) failed%s: %s",
                    backup.name,
                    schedule_name,
                    elapsed,
                    error.format(),
                )
            else:
                logger.exception(
                    "backup %r (%s) failed%s: unexpected error",
                    backup.name,
                    schedule_name,
                    elapsed,
                )
            if messages is not None:
                message = (
                    error.format() if isinstance(error, YaesmError) else "internal backup error"
                )
                messages.put(
                    {
                        "type": "result",
                        "ok": False,
                        "error": message,
                        "error_logged": logger.isEnabledFor(logging.ERROR),
                        "request_id": serialized_request_id,
                    }
                )
            raise
        else:
            if messages is not None:
                messages.put({"type": "result", "ok": True, "request_id": serialized_request_id})
        finally:
            current_backup.reset(backup_token)


class _ControlLogHandler(logging.Handler):
    """Send matching log messages to a control request."""

    def __init__(self, messages: queue.Queue[ControlMessage]) -> None:
        super().__init__()
        self.messages = messages

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.put({"type": "log", "message": record.getMessage()})


@dataclasses.dataclass
class _ActiveRequestLogStream:
    handler: _ControlLogHandler
    users: int = 0


_active_request_log_streams: dict[UUID, _ActiveRequestLogStream] = {}
_active_request_log_streams_lock = Lock()


@contextlib.contextmanager
def _stream_request_logs(
    backup_request_id: UUID | None,
    messages: queue.Queue[ControlMessage] | None,
) -> ty.Iterator[None]:
    """Stream log messages for one control request."""
    if backup_request_id is None or messages is None:
        yield
        return

    root_logger = logging.getLogger()
    with _active_request_log_streams_lock:
        stream = _active_request_log_streams.get(backup_request_id)
        if stream is None:
            handler = _ControlLogHandler(messages)
            handler.addFilter(RequestFilter(backup_request_id))
            stream = _ActiveRequestLogStream(handler)
            _active_request_log_streams[backup_request_id] = stream
            root_logger.addHandler(handler)
        stream.users += 1

    token = request_id.set(backup_request_id)
    try:
        yield
    finally:
        request_id.reset(token)
        with _active_request_log_streams_lock:
            stream.users -= 1
            if stream.users == 0:
                root_logger.removeHandler(stream.handler)
                del _active_request_log_streams[backup_request_id]
