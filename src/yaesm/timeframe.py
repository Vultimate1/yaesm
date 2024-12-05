import abc
from typing import final
import re

class Timeframe(abc.ABC):
    """Timeframe is an abstract base class for the different timeframe types.
    None of the Timeframe classes do validity checking on their initialization
    arguments, however all the tools for checking their validity are provided
    as static methods in the base Timeframe class.

    See the subclasses of Timeframe for more details.

    Also see test_timeframe.py for more examples of how to use Timeframes.
    """
    @staticmethod
    @abc.abstractmethod
    def required_config_settings():
        """Return the required configuration settings for the Timeframe subclass."""
        ...

    @final
    @staticmethod
    def tframe_types(names=False):
        """If 'names' is True, then return a list containing strings that represent
        the names of valid timeframes. If 'names' is False, then return a list of
        the classes of all the valid timeframes.
        """
        if names:
            return ["5minute", "hourly", "daily", "weekly", "monthly", "yearly"]
        else:
            return [FiveMinuteTimeframe, HourlyTimeframe, DailyTimeframe, WeeklyTimeframe, MonthlyTimeframe, YearlyTimeframe]

    @final
    @staticmethod
    def timespec_to_time(timespec):
        """Return a time from a timespec. A timespec is a string that matches the
        regex [0-9]{2}:[0-9]{2} where the first number before the : represents the hour,
        and the number after the : represents the minute. A time is a pair where the
        first element is an int representing the hour, and the second element is
        an int representing the minute. Note that this function expects to be
        given a valid timespec.

        See Timeframe.valid_timespec() for more information."""
        timespec_re = re.compile("([0-9]{2}):([0-9]{2})")
        re_result = timespec_re.match(timespec)
        hour = int(re_result.group(1))
        minute = int(re_result.group(2))
        return (hour, minute)

    @final
    @staticmethod
    def valid_weekday(weekday):
        """Return True if 'weekday' is a valid weekday, and return False otherwise."""
        return weekday in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

    @final
    @staticmethod
    def valid_monthday(monthday):
        """Return True if 'monthday' is a valid monthday, and return False otherwise."""
        return monthday >= 1 and monthday <= 31

    @final
    @staticmethod
    def valid_yearday(yearday, leap=False):
        """If 'leap' is True then return True if 'yearday' is a valid yearday in
        a leap year, and return False otherwise. If 'leap' is false then return
        True if 'yearday' is a valid yearday in a non-leap year, and return
        False otherwise.
        """
        max_yearday = 366 if leap else 365
        return yearday >= 1 and yearday <= max_yearday

    @final
    @staticmethod
    def valid_minute(minute):
        """Return True if 'minute' is a valid minute and return False otherwise."""
        return minute >= 0 and minute <= 59

    @final
    @staticmethod
    def valid_hour(hour):
        """Return True if 'hour' is a valid hour and return False otherwise."""
        return hour >= 0 and hour <= 23

    @final
    @staticmethod
    def valid_timespec(timespec):
        """Return True if 'time' is a valid timespec and return False otherwise.
        See 'Timeframe.timespec_to_time()' for more information on timespecs and
        times.
        """
        timespec_re = re.compile("([0-9]{2}):([0-9]{2})")
        if re_result := timespec_re.match(timespec):
            hour = int(re_result.group(1))
            minute = int(re_result.group(2))
            return Timeframe.valid_hour(hour) and Timeframe.valid_minute(minute)
        else:
            return False

    @final
    @staticmethod
    def valid_time(time):
        """Return True if time is a pair where its first element represents a
        valid hour and its second element represents a valid minute. Return
        False otherwise.
        """
        return valid_hour(time[0]) and valid_minute(time[1])

class FiveMinuteTimeframe(Timeframe):
    def __init__(self, keep):
        self.name = "5minute"
        self.keep = keep

    @staticmethod
    def required_config_settings():
        return ["5minute_keep"]

class HourlyTimeframe(Timeframe):
    def __init__(self, keep, minutes):
        self.name = "hourly"
        self.keep = keep
        self.minutes = minutes

    @staticmethod
    def required_config_settings():
        return ["hourly_keep", "hourly_minutes"]

class DailyTimeframe(Timeframe):
    def __init__(self, keep, times):
        self.name = "daily"
        self.keep = keep
        self.times = times

    @staticmethod
    def required_config_settings():
        return ["daily_keep", "daily_times"]

class WeeklyTimeframe(Timeframe):
    def __init__(self, keep, times, weekdays):
        self.name = "weekly"
        self.keep = keep
        self.times = times
        self.weekdays = weekdays

    @staticmethod
    def required_config_settings():
        return ["weekly_keep", "weekly_times", "weekly_days"]

class MonthlyTimeframe(Timeframe):
    def __init__(self, keep, times, monthdays):
        self.name = "monthly"
        self.keep = keep
        self.times = times
        self.monthdays = monthdays

    @staticmethod
    def required_config_settings():
        return ["monthly_keep", "monthly_times", "monthly_days"]

class YearlyTimeframe(Timeframe):
    def __init__(self, keep, times, yeardays):
        self.name = "yearly"
        self.keep = keep
        self.times = times
        self.yeardays = yeardays

    @staticmethod
    def required_config_settings():
        return ["yearly_keep", "yearly_times", "yearly_days"]
