from datetime import datetime
import heapq
import time
import croniter

def get_hours_and_minutes(times) -> tuple:
    """Returns a tuple of the list of hours and the list of minutes respectively.
    These lists a parallel.

    On failure (where a time is not of type `datetime.datetime`), both entries = `-1`."""
    minutes, hours = []
    for t in times:
        if type(t) != datetime:
            return (-1, -1)
        minutes.append(t.minute)
        hours.append(t.hour)
    return (hours, minutes)


class Scheduler:
    """Takes a list of `Timeframe`s as input, and creates a backup
    for each expired timeframe. If none have expired, this object
    will sleep (block) until the next timeframe expires."""

    VALID_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    DAYS_IN_MONTH_NO_LEAP_YEAR = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31}

    def __init__(self, timeframes):
        # This is a priority queue (`heapq`), containing tuples with elements:
        # 1st: The time since epoch (for sorting the queue + checking if expired)
        # 2nd: The croniter object of the timeframe
        # 3rd: The timeframe object
        self.timeframe_iters = []
        type_init_pairings = {
            "5minute" : self._five_minute_init,
            "hourly" : self._hourly_init,
            "daily" : self._daily_init,
            "weekly" : self._weekly_init,
            "monthly" : self._monthly_init,
            "yearly" : self._yearly_init
        }
        base = datetime.now()
        for timeframe in timeframes:
            try:
                type_init_pairings[timeframe.frame_type](base, timeframe, timeframe.kwargs)
            except KeyError:
                # TODO: What to do other than just re-raise?
                raise KeyError("'{0}' is not a valid timeframe type.".format(timeframe.frame_type))

    def _five_minute_init(self, base, obj, **kwargs):
        # Every 5 minutes...
        time_iter = croniter("*/5 * * * *", base)
        heapq.heappush(self.timeframe_iters, (time_iter.get_next(datetime), time_iter, obj))

    def _hourly_init(self, base, obj, **kwargs):
        # Assuming kwargs is handled outside of the class, this should NEVER throw a KeyError.
        minutes = ",".join(kwargs["minutes"])
        # At every minute in of every hour...
        time_iter = croniter("{0} * * * *".format(minutes), base)
        heapq.heappush(self.timeframe_iters, (time_iter.get_next(datetime), time_iter, obj))

    def _daily_init(self, base, obj, **kwargs):
        hours, minutes = get_hours_and_minutes(kwargs["times"])

        # Every day at each hour:minute...
        time_iters = list(
            map(lambda m, h: croniter("{0} {1} * * *".format(m, h), base), minutes, hours)
        )
        for time_iter in time_iters:
            heapq.heappush(self.timeframe_iters, (time_iter.get_next(datetime), time_iter, obj))

    def _weekly_init(self, base, obj, **kwargs):
        hours, minutes = get_hours_and_minutes(kwargs["times"])

        days = kwargs["days"]
        for i in range(len(days)):
            # croniter only needs the first 3 chars of the day.
            days[i] = days[i][0:3]
        days = ",".join(days)

        # Every specified day of the week at each hour:minute...
        time_iters = list(
            map(lambda m, h: croniter("{0} {1} * * {2}".format(m, h, days), base), minutes, hours)
        )
        for time_iter in time_iters:
            heapq.heappush(self.timeframe_iters, (time_iter.get_next(datetime), time_iter, obj))

    def _monthly_init(self, base, obj, **kwargs):
        hours, minutes = get_hours_and_minutes(kwargs["times"])
        days = ",".join(kwargs["days"])
        # Every month on each specified day at each hour:minute...
        time_iters = list(
            map(lambda m, h: croniter("{0} {1} {2} * *".format(m, h, days), base), minutes, hours)
        )
        for time_iter in time_iters:
            heapq.heappush(self.timeframe_iters, (time_iter.get_next(datetime), time_iter, obj))

    def _yearly_init(self, base, obj, **kwargs):
        hours, minutes = get_hours_and_minutes(kwargs["times"])
        days = kwargs["days"]
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
            heapq.heappush(self.timeframe_iters, (time_iter.get_next(datetime), time_iter, obj))

    def check_and_sleep(self):
        """Checks if any timeframe has expired,
        (TODO: takes a backup if any have done so),
        then sleeps until the earliest timeframe expires.

        Warning: This function blocks the thread. Run it in a separete"""
        present = datetime.now()
        while (len(self.timeframe_iters) >= 1 and self.timeframe_iters[0][0] <= present):
            # TODO Make a backup here!
            expired = heapq.heappop(self.timeframe_iters)
            next_time = expired[1].get_next(datetime)
            heapq.heappush(self.timeframe_iters, (next_time, expired[1], expired[2]))
        time.sleep(self.timeframe_iters[0][1].get_current(float))
