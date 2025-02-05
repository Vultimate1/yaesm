from datetime import datetime
import heapq
from threading import Thread
import time
from croniter import croniter


class TimeframeIter:
    def __init__(self, expiration, base, timeframe, func):
        self.expiration = expiration
        self.base = base
        self.timeframe = timeframe
        self.func = func

    # This is to ensure that heapq only compares by expiration and nothing else.
    def __lt__(self, other):
        return self.expiration < other.expiration


class Scheduler:
    """Takes a list of `Timeframe`s as input, and creates a backup
    for each expired timeframe. If none have expired, this object
    will sleep until the next timeframe expires."""

    DAYS_IN_MONTH_NO_LEAP_YEAR = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31}

    def __init__(self):
        self.timeframe_iters = []
        self.type_init_pairings = {
            "5minute" : self._five_minute_init,
            "hourly" : self._hourly_init,
            "daily" : self._daily_init,
            "weekly" : self._weekly_init,
            "monthly" : self._monthly_init,
            "yearly" : self._yearly_init
        }
        self.worker_threads = []

    def add_timeframe(self, timeframe, func):
        # This is a priority queue (`heapq`), containing `TimeframeIter`'s with elements:
        # 1st: The time since epoch (for sorting the queue + checking if expired)
        # 2nd: The croniter object
        # 3rd: The timeframe object
        # 4th: The function to call on expiry
        base = datetime.now()
        self.type_init_pairings[timeframe.name](base, timeframe, func)

    def _five_minute_init(self, base, obj, func):
        # Every 5 minutes...
        time_iter = croniter("*/5 * * * *", base)
        heapq.heappush(
            self.timeframe_iters,
            TimeframeIter(time_iter.get_next(datetime), time_iter, obj, func)
        )

    def _hourly_init(self, base, obj, func):
        # At every minute in of every hour...
        time_iters = list(
            map(lambda m: croniter("{0} * * * *".format(m), base), obj.minutes)
        )
        for time_iter in time_iters:
            heapq.heappush(
                self.timeframe_iters,
                TimeframeIter(time_iter.get_next(datetime), time_iter, obj, func)
            )

    def _daily_init(self, base, obj, func):
        hours, minutes = zip(*obj.times)

        # Every day at each hour:minute...
        time_iters = list(
            map(lambda m, h: croniter("{0} {1} * * *".format(m, h), base), minutes, hours)
        )
        for time_iter in time_iters:
            heapq.heappush(
                self.timeframe_iters,
                TimeframeIter(time_iter.get_next(datetime), time_iter, obj, func)
            )

    def _weekly_init(self, base, obj, func):
        hours, minutes = zip(*obj.times)

        days = obj.weekdays
        for i in range(len(days)):
            # croniter only needs the first 3 chars of the day.
            days[i] = days[i][0:3]
        days = ",".join(days)

        # Every specified day of the week at each hour:minute...
        time_iters = list(
            map(lambda m, h: croniter("{0} {1} * * {2}".format(m, h, days), base), minutes, hours)
        )
        for time_iter in time_iters:
            heapq.heappush(
                self.timeframe_iters,
                TimeframeIter(time_iter.get_next(datetime), time_iter, obj, func)
            )

    def _monthly_init(self, base, obj, func):
        hours, minutes = zip(*obj.times)
        days = ",".join(obj.monthdays)
        # Every month on each specified day at each hour:minute...
        time_iters = list(
            map(lambda m, h: croniter("{0} {1} {2} * *".format(m, h, days), base), minutes, hours)
        )
        for time_iter in time_iters:
            heapq.heappush(
                self.timeframe_iters,
                TimeframeIter(time_iter.get_next(datetime), time_iter, obj, func)
            )

    def _yearly_init(self, base, obj, func):
        hours, minutes = zip(*obj.times)
        days = obj.yeardays
        months = []
        for i in range(len(days)):
            month = 1
            for m in self.DAYS_IN_MONTH_NO_LEAP_YEAR:
                if days[i] > self.DAYS_IN_MONTH_NO_LEAP_YEAR[m]:
                    days[i] -= self.DAYS_IN_MONTH_NO_LEAP_YEAR[m]
                    month += 1
            months.append(month)

        # Every specified day of the year at each hour:minute...
        time_iters = list(
            map(
                lambda mi, h, d, mo: croniter("{0} {1} {2} {3} *".format(mi, h, d, mo), base),
                minutes, hours, days, months
            )
        )
        for time_iter in time_iters:
            heapq.heappush(
                self.timeframe_iters,
                TimeframeIter(time_iter.get_next(datetime), time_iter, obj, func)
            )

    def _join_dead_threads(self):
        dead_threads = []
        for thread in self.worker_threads:
            if not thread.is_alive():
                thread.join()
                dead_threads.append(thread)
        self.worker_threads = list(filter(lambda t: t not in dead_threads, self.worker_threads))

    def check_for_expired(self):
        """Checks if any timeframe has expired.

        Warning: This function may block forever. Spin it off into its own thread."""
        present = datetime.now()
        while len(self.timeframe_iters) >= 1 and self.timeframe_iters[0].expiration <= present:
            expired = heapq.heappop(self.timeframe_iters)

            self.worker_threads.append(Thread(target=expired.func, daemon=True))
            self.worker_threads[-1].start()

            next_time = expired.base.get_next(datetime)
            heapq.heappush(
                self.timeframe_iters,
                TimeframeIter(next_time, expired.base, expired.timeframe, expired.func)
            )
            present = datetime.now()
            self._join_dead_threads()

        while len(self.worker_threads) > 0:
            self._join_dead_threads()

    def sleep_until_next_timeframe(self):
        """Sleeps until the earliest timeframe expires.

        It's recommended to spin this function off into its own thread."""
        # Frequently checking the datetime like this so that updates to
        # `freeze_time` in tests go through.
        while self.timeframe_iters[0].expiration > datetime.now():
            time.sleep(1)
