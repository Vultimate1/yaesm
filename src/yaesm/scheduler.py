"""Scheduling of configured backups."""

import logging
import queue
from _thread import LockType
from datetime import datetime
from threading import Lock
from uuid import UUID, uuid4

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.base import BaseTrigger

import yaesm.ty as ty
from yaesm.backup import Backup
from yaesm.config import Config
from yaesm.control import ControlMessage
from yaesm.errors import YaesmError
from yaesm.logging import RequestFilter, request_id
from yaesm.schedule import OnDemandSchedule

logger = logging.getLogger(__name__)


class SchedulerError(YaesmError):
    """Raised when a backup cannot be scheduled."""


class Scheduler:
    """Schedule and run configured backup jobs."""

    def __init__(self, config: Config) -> None:
        self._lock = Lock()
        self._backup_locks: dict[str, LockType] = {}
        self._request_messages: dict[UUID, queue.Queue[ControlMessage]] = {}
        self._timer_job_ids: set[str] = set()
        self._scheduler = BlockingScheduler(
            executors={"default": ThreadPoolExecutor(max_workers=10)},
            job_defaults={"max_instances": 1},
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
            for backup in config.backups.values():
                for schedule in backup.schedules:
                    for index, trigger in enumerate(schedule.timer_triggers()):
                        job_id = f"{backup.name}:{schedule.name}:{index}"
                        self._add_job(
                            backup,
                            schedule.name,
                            config.backups,
                            trigger=trigger,
                            job_id=job_id,
                        )
                        self._timer_job_ids.add(job_id)

    def enqueue_backup(self, backup_name: str, schedule_name: str | None = None) -> UUID:
        """Queue a configured backup for immediate execution."""
        with self._lock:
            config = self._config
            backup = config.backups.get(backup_name)
            if backup is None:
                raise SchedulerError(f"unknown backup: {backup_name!r}")

            schedules = [
                schedule
                for schedule in backup.schedules
                if isinstance(schedule.implementation, OnDemandSchedule)
            ]
            if schedule_name is None:
                if not schedules:
                    raise SchedulerError(f"backup {backup_name!r} has no on-demand schedule")
                if len(schedules) > 1:
                    raise SchedulerError(f"backup {backup_name!r} has multiple on-demand schedules")
                schedule_name = schedules[0].name
            elif not any(schedule.name == schedule_name for schedule in backup.schedules):
                raise SchedulerError(f"backup {backup_name!r} has no schedule {schedule_name!r}")
            elif not any(schedule.name == schedule_name for schedule in schedules):
                raise SchedulerError(
                    f"schedule {schedule_name!r} for backup {backup_name!r} is not on-demand"
                )

            request_id = uuid4()
            self._request_messages[request_id] = queue.Queue()
            try:
                self._add_job(
                    backup,
                    schedule_name,
                    config.backups,
                    request_id=request_id,
                    job_id=str(request_id),
                )
            except BaseException:
                del self._request_messages[request_id]
                raise
        return request_id

    def request_messages(self, request_id: UUID) -> ty.Iterator[ControlMessage]:
        """Yield a queued backup's logs followed by its result."""
        with self._lock:
            try:
                messages = self._request_messages[request_id]
            except KeyError as error:
                raise SchedulerError(f"unknown backup request: {request_id!r}") from error

        try:
            while True:
                message = messages.get()
                yield message
                if message.get("type") == "result":
                    return
        finally:
            with self._lock:
                self._request_messages.pop(request_id, None)

    def _add_job(
        self,
        backup: Backup,
        schedule_name: str,
        backups: ty.Mapping[str, Backup],
        *,
        job_id: str,
        trigger: BaseTrigger | None = None,
        request_id: UUID | None = None,
    ) -> None:
        backup_lock = self._backup_locks.setdefault(backup.name, Lock())
        messages = None if request_id is None else self._request_messages[request_id]
        self._scheduler.add_job(
            _execute_backup,
            trigger=trigger,
            args=(backup, schedule_name, backups, request_id, messages, backup_lock),
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
    backup_lock: LockType,
) -> None:
    handler = None
    token = None
    serialized_request_id = None if backup_request_id is None else str(backup_request_id)
    if backup_request_id is not None and messages is not None:
        handler = _ControlLogHandler(messages)
        handler.addFilter(RequestFilter(backup_request_id))
        logging.getLogger().addHandler(handler)
        token = request_id.set(backup_request_id)

    try:
        with backup_lock:
            logger.info("backup %r (%s) started", backup.name, schedule_name)
            backup.execute(schedule_name, datetime.now(), backups)
            logger.info("backup %r (%s) completed", backup.name, schedule_name)
    except BaseException as error:
        if messages is not None:
            message = error.format() if isinstance(error, YaesmError) else "internal backup error"
            messages.put(
                {
                    "type": "result",
                    "ok": False,
                    "error": message,
                    "request_id": serialized_request_id,
                }
            )
        raise
    else:
        if messages is not None:
            messages.put({"type": "result", "ok": True, "request_id": serialized_request_id})
    finally:
        if token is not None:
            request_id.reset(token)
        if handler is not None:
            logging.getLogger().removeHandler(handler)


class _ControlLogHandler(logging.Handler):
    """Send matching log messages to a control request."""

    def __init__(self, messages: queue.Queue[ControlMessage]) -> None:
        super().__init__()
        self.messages = messages

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.put({"type": "log", "message": self.format(record)})
