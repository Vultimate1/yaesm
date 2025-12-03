"""src/yaesm/timeframe.py"""
import dataclasses
from typing import final

class Timeframe():
    """Timeframe is a base class for the different timeframe types. None of the
    Timeframe classes do validity checking on their initialization arguments.

    See the subclasses of Timeframe for more details.

    Also see test_timeframe.py for more examples of how to use Timeframes.
    """
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

@dataclasses.dataclass
class FiveMinuteTimeframe(Timeframe):
    """The 5minute timeframe represents backups to be taken every 5 minutes.
    The 'keep' instance variable is a whole number that represents the maximum
    number of backups to keep for this timeframe before deleting old ones.
    """
    def __init__(self, keep):
        self.name = "5minute"
        self.keep = keep

@dataclasses.dataclass
class HourlyTimeframe(Timeframe):
    """The hourly timeframe represents backups to be taken every hour at some set
    of minutes. The 'minutes' instance variable is a list of ints with values in
    range of 0-59. The 'keep' instance variable is a whole number that represents
    the maximum number of backups to keep for this timeframe before deleting old
    ones.
    """
    def __init__(self, keep, minutes):
        self.name = "hourly"
        self.keep = keep
        self.minutes = minutes

@dataclasses.dataclass
class DailyTimeframe(Timeframe):
    """The daily timeframe represents backups to be taken every day at some set
    of times in the day. The 'times' instance variable represents the times in
    the day to perform backups. The 'times' instance variable is a list of pairs
    of hours and minutes, where the hour is an int with a value in range 0-23,
    and the minute is an int in range 0-59. The 'keep' instance variable is a
    whole number that represents the maximum number of backups to keep for this
    timeframe before deleting old ones.
    """
    def __init__(self, keep, times):
        self.name = "daily"
        self.keep = keep
        self.times = times

@dataclasses.dataclass
class WeeklyTimeframe(Timeframe):
    """The weekly timeframe represents backups to be taken every week at some
    set of days in the week, at some set of times in those days. The 'weekdays'
    instance variable is a set of strings denoting the weekdays to perform
    backups (i.e. monday, thursday, etc). The 'times' instance variable represents
    the times in the day to perform backups. The 'times' instance variable is a
    list of pairs of hours and minutes where the hour is an int with a value in
    range 0-23, and the minute is an int in range 0-59. The 'keep' instance
    variable is a whole number that represents the maximum number of backups to
    keep for this timeframe before deleting old ones.
    """
    def __init__(self, keep, times, weekdays):
        self.name = "weekly"
        self.keep = keep
        self.times = times
        self.weekdays = weekdays

@dataclasses.dataclass
class MonthlyTimeframe(Timeframe):
    """The monthly timeframe represents backups to be taken every month at some
    set of days in the month, and at some set of times in those days. The
    'monthdays' instance variable is a set of ints denoting the days in the
    month to perform backups (starting at 1). The 'times' instance variable
    represents the times in the day to perform backups. The 'times' instance
    variable is a list of pairs of hours and minutes where the hour is an int
    with a value in range 0-23, and the minute is an int in range 0-59. The
    'keep' instance variable is a whole number that represents the maximum
    number of backups to keep for this timeframe before deleting old ones.
    """
    def __init__(self, keep, times, monthdays):
        self.name = "monthly"
        self.keep = keep
        self.times = times
        self.monthdays = monthdays

@dataclasses.dataclass
class YearlyTimeframe(Timeframe):
    """The yearly timeframe represents backups to be taken every year at some
    set of days in the year, and at some set of times in those days. The
    'yeardays' instance variable is a set of ints denoting the days in the year
    to perform backups (starting at 1). The 'times' instance variable represents
    the times in the day to perform backups. The 'times' instance variable is a
    list of pairs of hours and minutes where the hour is an int with a value in
    range 0-23, and the minute is an int in range 0-59. The 'keep' instance
    variable is a whole number that represents the maximum number of backups to
    keep for this timeframe before deleting old ones.
    """
    def __init__(self, keep, times, yeardays):
        self.name = "yearly"
        self.keep = keep
        self.times = times
        self.yeardays = yeardays
