"""src/yaesm/scheduler.py"""

from datetime import datetime, timedelta

import apscheduler.schedulers.blocking
import apscheduler.events

from yaesm.logging import Logging
from yaesm.timeframe import Timeframe, FiveMinuteTimeframe, HourlyTimeframe, DailyTimeframe, \
    WeeklyTimeframe, MonthlyTimeframe

class Scheduler:
    def __init__(self):
        self._apscheduler = apscheduler.schedulers.blocking.BlockingScheduler()
        Logging.get("apscheduler").propagate = False
        Logging.get("apscheduler").setLevel("CRITICAL")
        self._apscheduler.add_listener(
            lambda event: Logging.get()
                .info("%s - successful backup", self._job_name(event.job_id)),
            apscheduler.events.EVENT_JOB_EXECUTED
        )
        self._apscheduler.add_listener(
            lambda event: Logging.get()
                .error("%s - %s", self._job_name(event.job_id), event.exception),
            apscheduler.events.EVENT_JOB_ERROR
        )
        self._apscheduler.add_listener(
            lambda event: Logging.get()
                .error("%s - missed backup", self._job_name(event.job_id)),
            apscheduler.events.EVENT_JOB_MISSED
        )

    def start(self):
        """Start the scheduler. Since the scheduler blocks this function will
        not return.
        """
        self._apscheduler.start()

    def _job_name(self, job_id):
        """Return name of APScheduler job with job id `job_id`."""
        return self._apscheduler.get_job(job_id).name

    def stop(self, force=False):
        """Stop the scheduler gracefully."""
        self._apscheduler.shutdown(wait=not force)

    def add_backups(self, backups):
        """Schedule every Backup in `backups` to have their backend's `do_backup()`
        function executed at the times denoted by the backup's Timeframes.
        """
        for backup in backups:
            for timeframe in backup.timeframes:
                job_name = f"{backup.name} ({timeframe.name})"
                self._add_job(job_name,
                              lambda b=backup, t=timeframe: b.backend.do_backup(b,t), timeframe)

    def _add_job(self, name, func, timeframe):
        """Schedule an arbitrary function (`func`) to be run at times according to `timeframe`."""
        if isinstance(timeframe, FiveMinuteTimeframe):
            self._apscheduler.add_job(func, "cron", minute="*/5", name=name)
        elif isinstance(timeframe, HourlyTimeframe):
            minute_str = ','.join(str(m) for m in timeframe.minutes)
            self._apscheduler.add_job(func, "cron", minute=minute_str, name=name)
        elif isinstance(timeframe, DailyTimeframe):
            for time in timeframe.times:
                hour, minute = time
                self._apscheduler.add_job(func, "cron", minute=minute, hour=hour, name=name)
        elif isinstance(timeframe, WeeklyTimeframe):
            weekday_str = ",".join(str(Timeframe.weekday_num(d)) for d in timeframe.weekdays)
            for time in timeframe.times:
                hour, minute = time
                self._apscheduler.add_job(func, "cron", minute=minute, hour=hour,
                                          day_of_week=weekday_str, name=name)
        elif isinstance(timeframe, MonthlyTimeframe):
            for monthday in timeframe.monthdays:
                for time in timeframe.times:
                    hour, minute = time
                    self._apscheduler.add_job(func, "cron", minute=minute, hour=hour,
                                              day=monthday, name=name)
        else: # YearlyTimeframe
            for yearday in timeframe.yeardays:
                # Use non-leap year for conversion
                dt = datetime(1999, 1, 1) + timedelta(days=yearday-1)
                month = dt.month
                day = dt.day
                for time in timeframe.times:
                    hour, minute = time
                    self._apscheduler.add_job(func, "cron", minute=minute, hour=hour,
                                              day=day, month=month, name=name)
