"""src/yaesm/timeframe.py"""
import dataclasses
from typing import final

class Timeframe():
    """`Timeframe` is a base class for the different timeframe types. None of the
    Timeframe classes do validity checking on their initialization arguments.

    See the subclasses of `Timeframe` for more details.

    Also see test_timeframe.py for examples of how to use `Timeframe`'s."""
    @final
    @staticmethod
    def tframe_types(names=False) -> list:
        """If `names` is `True`, return a list containing the names of all timeframe types
        as strings. Otherwise return a list of all the timeframe type subclasses."""
        if names:
            return ["5minute", "hourly", "daily", "weekly", "monthly", "yearly"]
        return [FiveMinuteTimeframe, HourlyTimeframe, DailyTimeframe, WeeklyTimeframe,
                MonthlyTimeframe, YearlyTimeframe]

    @final
    @staticmethod
    def weekday_num(weekday):
        # monday is first day to adhere to apscheduler
        weekday_num_map = {"monday":0,"tuesday":1,"wednesday":2,"thursday":3,"friday":4,"saturday":5,"sunday":6}
        return weekday_num_map[weekday]

@dataclasses.dataclass
class FiveMinuteTimeframe(Timeframe):
    """`FiveMinuteTimeframe` represents backups to be taken every 5 minutes.

    The `keep` instance variable is an `int` that represents the maximum
    number of backups to keep for this timeframe before deleting old ones."""
    name = "5minute"
    keep: int

@dataclasses.dataclass
class HourlyTimeframe(Timeframe):
    """`HourlyTimeframe` represents backups to be taken every hour at some set
    of `minutes`.

    The `minutes` instance variable is a list of `int` with values in
    range of 0-59. The `keep` instance variable is an `int` that represents
    the maximum number of backups to keep for this timeframe before deleting old
    ones."""
    name = "hourly"
    keep: int
    minutes: list[int]

@dataclasses.dataclass
class DailyTimeframe(Timeframe):
    """`DailyTimeframe` represents backups to be taken every day at some set
    of times in the day.

    The `times` instance variable represents the times in
    the day to perform backups. It is a list of pairs of hours and minutes,
    where the hour is an `int` in range 0-23, and the minute is an `int`
    in range 0-59. The `keep` instance variable is an `int` that represents the
    maximum number of backups to keep for this timeframe before deleting old ones."""
    name = "daily"
    keep: int
    times: list[list[int, int]]

@dataclasses.dataclass
class WeeklyTimeframe(Timeframe):
    """`WeeklyTimeframe` represents backups to be taken every week at some
    set of days in the week, at some set of times in those days.

    The `weekdays` instance variable is a set of strings denoting the weekdays to perform
    backups (i.e. monday, thursday, etc). The `times` instance variable represents
    the times in the day to perform backups. It is a list of pairs of hours and
    minutes, where the hour is an `int` in range 0-23, and the minute is an `int`
    in range 0-59. The `keep` instance variable is an `int` that represents the
    maximum number of backups to keep for this timeframe before deleting old ones."""
    name = "weekly"
    keep: int
    times: list[list[int, int]]
    weekdays: list[str]

@dataclasses.dataclass
class MonthlyTimeframe(Timeframe):
    """`MonthlyTimeframe` represents backups to be taken every month at some
    set of days in the month, and at some set of times in those days.

    The `monthdays` instance variable is a set of `int` denoting the days in the
    month to perform backups (starting at 1). The `times` instance variable
    represents the times in the day to perform backups. The `times` instance
    variable represents the times in the day to perform backups. It is a list of
    pairs of hours and minutes, where the hour is an `int` in range 0-23, and the
    minute is an `int` in range 0-59. The `keep` instance variable is an `int`
    that represents the maximum number of backups to keep for this timeframe before
    deleting old ones."""
    name = "monthly"
    keep: int
    times: list[list[int, int]]
    monthdays: list[int]

@dataclasses.dataclass
class YearlyTimeframe(Timeframe):
    """`YearlyTimeframe` represents backups to be taken every year at some
    set of days in the year, and at some set of times in those days.

    The `yeardays` instance variable is a set of `int` denoting the days in the year
    to perform backups (starting at 1). The `times` instance variable represents
    the times in the day to perform backups. It is a list of pairs of hours and
    minutes, where the hour is an `int` in range 0-23, and the minute is an `int`
    in range 0-59. The `keep` instance variable is an `int` that represents the
    maximum number of backups to keep for this timeframe before deleting old ones."""
    name = "yearly"
    keep: int
    times: list[list[int, int]]
    yeardays: list[int]
