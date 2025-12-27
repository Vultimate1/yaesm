import apscheduler.schedulers.blocking
import apscheduler.events
from datetime import datetime, timedelta

from yaesm.logging import logger
from yaesm.timeframe import Timeframe, FiveMinuteTimeframe, HourlyTimeframe, DailyTimeframe, WeeklyTimeframe, MonthlyTimeframe

class Scheduler:
    def __init__(self):
        self._apscheduler = apscheduler.schedulers.blocking.BlockingScheduler()
        self._apscheduler.add_listener(
            lambda event: logger().error(str(event.exception)),
            apscheduler.events.EVENT_JOB_ERROR
        )

    def start(self):
        """Start the scheduler. Since the scheduler blocks this function will
        not return.
        """
        self._apscheduler.start()

    def stop(self, force=False):
        """Stop the scheduler gracefully."""
        self._apscheduler.shutdown(wait=not(force))

    def add_backups(self, backups):
        """Schedule every Backup in `backups` to have their backend's `do_backup()`
        function executed at the times denoted by the backup's Timeframes.
        """
        for backup in backups:
            for timeframe in backup.timeframes:
                self._add_job(lambda b=backup, t=timeframe: b.backend.do_backup(b,t), timeframe)

    def _add_job(self, func, timeframe):
        """Schedule an arbitrary function (`func`) to be run at times according to `timeframe`."""
        if isinstance(timeframe, FiveMinuteTimeframe):
            self._apscheduler.add_job(func, "cron", minute="*/5")
        elif isinstance(timeframe, HourlyTimeframe):
            minute_str = ','.join(str(m) for m in timeframe.minutes)
            self._apscheduler.add_job(func, "cron", minute=minute_str)
        elif isinstance(timeframe, DailyTimeframe):
            for time in timeframe.times:
                hour, minute = time
                self._apscheduler.add_job(func, "cron", minute=minute, hour=hour)
        elif isinstance(timeframe, WeeklyTimeframe):
            weekday_str = ",".join(str(Timeframe.weekday_num(d)) for d in timeframe.weekdays)
            for time in timeframe.times:
                hour, minute = time
                self._apscheduler.add_job(func, "cron", minute=minute, hour=hour, day_of_week=weekday_str)
        elif isinstance(timeframe, MonthlyTimeframe):
            for monthday in timeframe.monthdays:
                for time in timeframe.times:
                    hour, minute = time
                    self._apscheduler.add_job(func, "cron", minute=minute, hour=hour, day=monthday)
        else: # YearlyTimeframe
            for yearday in timeframe.yeardays:
                dt = datetime(1999, 1, 1) + timedelta(days=yearday - 1) # Use non-leap year for conversion
                month = dt.month
                day = dt.day
                for time in timeframe.times:
                    hour, minute = time
                    self._apscheduler.add_job(func, "cron", minute=minute, hour=hour, day=day, month=month)
