"""Scheduling of configured backups."""

import uuid
from datetime import datetime
from threading import Lock

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.base import BaseTrigger

import yaesm.ty as ty
from yaesm.backup import Backup
from yaesm.config import Config
from yaesm.errors import YaesmError


class SchedulerError(YaesmError):
    """Raised when a backup cannot be scheduled."""


class Scheduler:
    """Schedule and run configured backup jobs."""

    def __init__(self, config: Config) -> None:
        self._lock = Lock()
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

    def enqueue_backup(self, backup_name: str, schedule_name: str) -> str:
        """Queue a configured backup for immediate execution."""
        with self._lock:
            config = self._config
            backup = config.backups.get(backup_name)
            if backup is None:
                raise SchedulerError(f"unknown backup: {backup_name!r}")
            if not any(schedule.name == schedule_name for schedule in backup.schedules):
                raise SchedulerError(f"backup {backup_name!r} has no schedule {schedule_name!r}")

            request_id = uuid.uuid4().hex
            self._add_job(
                backup,
                schedule_name,
                config.backups,
                request_id=request_id,
                job_id=request_id,
            )
        return request_id

    def _add_job(
        self,
        backup: Backup,
        schedule_name: str,
        backups: ty.Mapping[str, Backup],
        *,
        job_id: str,
        trigger: BaseTrigger | None = None,
        request_id: str | None = None,
    ) -> None:
        self._scheduler.add_job(
            _execute_backup,
            trigger=trigger,
            args=(backup, schedule_name, backups, request_id),
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
    _request_id: str | None,
) -> None:
    backup.execute(schedule_name, datetime.now(), backups)
