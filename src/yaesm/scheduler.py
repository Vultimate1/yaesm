from datetime import datetime
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
        # This is a 2D list with pairings of `Timeframe` and croniter iterator respectively.
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
        self.timeframe_iters.append(croniter("*/5 * * * *", base))

    def _hourly_init(self, base, obj, **kwargs):
        # Assuming kwargs is handled outside of the class, this should NEVER throw a KeyError.
        minutes = kwargs["minutes"]

        for m in minutes:
            if type(m) != int:
                raise TypeError("All values in 'hourly_minutes' must be integers.")

        minutes = ",".join(minutes)
        # At every minute in of every hour...
        self.timeframe_iters.append([croniter("{0} * * * *".format(minutes), base)])

    def _daily_init(self, base, obj, **kwargs):
        hours, minutes = get_hours_and_minutes(kwargs["times"])
        if hours == -1:
            raise TypeError("All values in 'daily_times' must be timestamps.")

        # Every day at each hour:minute...
        self.timeframe_iters = list(
            map(lambda m, h: croniter("{0} {1} * * *".format(m, h), base), minutes, hours)
        )

    def _weekly_init(self, base, obj, **kwargs):
        hours, minutes = get_hours_and_minutes(kwargs["times"])
        if hours == -1:
            raise TypeError("All values in 'weekly_times' must be timestamps.")

        days = kwargs["days"]
        for i in range(len(days)):
            if type(days[i]) != str or days[i] not in self.VALID_DAYS:
                raise TypeError("All values in 'weekly_days' must be valid days of the week.")
            # croniter only needs the first 3 chars of the day.
            days[i] = days[i][0:3]

        days = ",".join(days)
        # Every specified day of the week at each hour:minute...
        self.timeframe_iters = list(
            map(lambda m, h: croniter("{0} {1} * * {2}".format(m, h, days), base), minutes, hours)
        )

    def _monthly_init(self, base, obj, **kwargs):
        hours, minutes = get_hours_and_minutes(kwargs["times"])
        if hours == -1:
            raise TypeError("All values in 'monthly_times' must be timestamps.")

        days = kwargs["days"]
        for i in range(len(days)):
            if type(days[i]) != int or days[i] < 1 or days[i] > 31:
                raise TypeError("All values in 'monthly_days' must be integers in range [1, 31].")

        days = ",".join(days)
        # Every month on each specified day at each hour:minute...
        self.timeframe_iters = list(
            map(lambda m, h: croniter("{0} {1} {2} * *".format(m, h, days), base), minutes, hours)
        )

    def _yearly_init(self, base, obj, **kwargs):
        hours, minutes = get_hours_and_minutes(kwargs["times"])
        if hours == -1:
            raise TypeError("All values in 'yearly_times' must be timestamps.")

        days = kwargs["days"]
        months = []
        for i in range(len(days)):
            # TODO: Do we want to account for leap years? (366 days instead of 365)
            if type(days[i]) != int or days[i] < 1 or days[i] > 365:
                raise TypeError(
                    "All values in 'yearly_days' must be integers in range [1, 365]."
                )
            month = 1
            for m in self.DAYS_IN_MONTH_NO_LEAP_YEAR:
                if days[i] > self.DAYS_IN_MONTH_NO_LEAP_YEAR[m]:
                    days[i] -= self.DAYS_IN_MONTH_NO_LEAP_YEAR[m]
                    month += 1
            months.append(month)

        # Every specified day of the year at each hour:minute...
        self.timeframe_iters = list(
            map(
                lambda mi, h, d, mo: croniter("{0} {1} {2} {3} *".format(mi, h, d, mo), base),
                minutes, hours, days, months
            )
        )

    def check_and_sleep(self):
        """Checks if any timeframe has expired,
        (TODO: takes a backup if any have done so),
        then sleeps until the earliest timeframe expires.

        Warning: This function blocks the thread. Run it in a separete"""
        present = datetime.now()
        next_timeframe = None
        for time_iter, obj in self.timeframe_iters:
            if time_iter.get_next(datetime) <= present:
                # TODO Make a backup here!
                obj.check_keep()
            else:
                if time_iter.get_prev(datetime) < next_timeframe:
                    next_timeframe = time_iter
        time.sleep(next_timeframe.get_current(float))
