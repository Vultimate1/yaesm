"""src/yaesm/scheduler.py."""

import logging
from datetime import datetime, timedelta

import apscheduler.events
import apscheduler.executors.pool
import apscheduler.schedulers.blocking

import yaesm.ty as ty
from yaesm.backup import Backup
from yaesm.timeframe import (
    DailyTimeframe,
    FiveMinuteTimeframe,
    HourlyTimeframe,
    MonthlyTimeframe,
    WeeklyTimeframe,
    weekday_num,
)

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self) -> None:
        self._apscheduler = apscheduler.schedulers.blocking.BlockingScheduler(
            executors={
                "default": apscheduler.executors.pool.ThreadPoolExecutor(max_workers=10),
            },
            job_defaults={"max_instances": 1},
        )
        logging.getLogger("apscheduler").propagate = False
        logging.getLogger("apscheduler").setLevel("CRITICAL")
        self._apscheduler.add_listener(
            lambda _event: logger.info("scheduler started"),
            apscheduler.events.EVENT_SCHEDULER_STARTED,
        )
        self._apscheduler.add_listener(
            lambda event: logger.info("%s - successful backup", self._job_name(event.job_id)),
            apscheduler.events.EVENT_JOB_EXECUTED,
        )
        self._apscheduler.add_listener(
            lambda event: logger.error("%s - %s", self._job_name(event.job_id), event.exception),
            apscheduler.events.EVENT_JOB_ERROR,
        )
        self._apscheduler.add_listener(
            lambda event: logger.warning("%s - missed backup", self._job_name(event.job_id)),
            apscheduler.events.EVENT_JOB_MISSED,
        )
        self._apscheduler.add_listener(
            lambda event: logger.warning(
                "%s - skipped: previous run still running (scheduled too frequently?)",
                self._job_name(event.job_id),
            ),
            apscheduler.events.EVENT_JOB_MAX_INSTANCES,
        )

    def start(self) -> None:
        """Start the scheduler if it has not already been started. Since the
        scheduler blocks this function may not return.
        """
        if not self._apscheduler.running:
            self._apscheduler.start()

    def stop(self, force: bool = False) -> None:
        """Stop the scheduler gracefully."""
        self._apscheduler.shutdown(wait=not force)

    def add_backups(self, backups: list[Backup]) -> None:
        """Schedule every Backup in `backups` to have their backend's `do_backup()`
        function executed at the times denoted by the backup's Timeframes.
        """
        for backup in backups:
            for timeframe in backup.timeframes:
                job_name = f"{backup.name} ({timeframe.name})"
                self._add_job(
                    job_name, lambda b=backup, t=timeframe: b.backend.do_backup(b, t), timeframe
                )

    def _job_name(self, job_id: str) -> str:
        """Return name of the APScheduler job with id `job_id`."""
        return self._apscheduler.get_job(job_id).name

    def _add_job(self, name: str, func: ty.Callable[[], None], timeframe: ty.Any) -> None:
        """Schedule an arbitrary function (`func`) to be run at times according to `timeframe`."""
        if isinstance(timeframe, FiveMinuteTimeframe):
            self._apscheduler.add_job(func, "cron", minute="*/5", name=name)
        elif isinstance(timeframe, HourlyTimeframe):
            minute_str = ",".join(str(m) for m in timeframe.minutes)
            self._apscheduler.add_job(func, "cron", minute=minute_str, name=name)
        elif isinstance(timeframe, DailyTimeframe):
            for time in timeframe.times:
                hour, minute = time
                self._apscheduler.add_job(func, "cron", minute=minute, hour=hour, name=name)
        elif isinstance(timeframe, WeeklyTimeframe):
            weekday_str = ",".join(str(weekday_num(d)) for d in timeframe.weekdays)
            for time in timeframe.times:
                hour, minute = time
                self._apscheduler.add_job(
                    func, "cron", minute=minute, hour=hour, day_of_week=weekday_str, name=name
                )
        elif isinstance(timeframe, MonthlyTimeframe):
            for monthday in timeframe.monthdays:
                for time in timeframe.times:
                    hour, minute = time
                    self._apscheduler.add_job(
                        func, "cron", minute=minute, hour=hour, day=monthday, name=name
                    )
        else:  # YearlyTimeframe
            for yearday in timeframe.yeardays:
                # Use non-leap year for conversion
                dt = datetime(1999, 1, 1) + timedelta(days=yearday - 1)
                month = dt.month
                day = dt.day
                for time in timeframe.times:
                    hour, minute = time
                    self._apscheduler.add_job(
                        func, "cron", minute=minute, hour=hour, day=day, month=month, name=name
                    )
