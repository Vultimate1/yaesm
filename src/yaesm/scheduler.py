"""Scheduling of configured backups."""

from datetime import datetime

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler

import yaesm.ty as ty
from yaesm.backup import Backup
from yaesm.config import Config


class Scheduler:
    """Schedule and run the timer-based jobs in a configuration."""

    def __init__(self, config: Config) -> None:
        self._scheduler = BlockingScheduler(
            executors={"default": ThreadPoolExecutor(max_workers=10)},
            job_defaults={"max_instances": 1},
        )
        self.replace_config(config)

    def replace_config(self, config: Config) -> None:
        """Replace scheduled jobs without interrupting running jobs."""
        self._scheduler.remove_all_jobs()
        for backup in config.backups.values():
            for schedule in backup.schedules:
                for index, trigger in enumerate(schedule.timer_triggers()):
                    self._scheduler.add_job(
                        _execute_backup,
                        trigger=trigger,
                        args=(backup, schedule.name, config.backups),
                        id=f"{backup.name}:{schedule.name}:{index}",
                        name=f"{backup.name} ({schedule.name})",
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
) -> None:
    backup.execute(schedule_name, datetime.now(), backups)
