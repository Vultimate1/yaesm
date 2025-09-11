import pytest
import random

from yaesm.timeframe import Timeframe, FiveMinuteTimeframe, HourlyTimeframe, DailyTimeframe, WeeklyTimeframe, MonthlyTimeframe, YearlyTimeframe

def test_FiveMinuteTimeframe():
    assert FiveMinuteTimeframe.required_config_settings() == ["5minute_keep"]
    tf = FiveMinuteTimeframe(10)
    assert tf.name == "5minute"
    assert tf.keep == 10

def test_HourlyTimeframe():
    assert HourlyTimeframe.required_config_settings() == ["hourly_keep", "hourly_minutes"]
    tf = HourlyTimeframe(10, [0,1,30,59])
    assert tf.name == "hourly"
    assert tf.keep == 10
    assert tf.minutes == [0,1,30,59]

def test_DailyTimeframe():
    assert DailyTimeframe.required_config_settings() == ["daily_keep", "daily_times"]
    tf = DailyTimeframe(10, [(23,59), (12,30)])
    assert tf.name == "daily"
    assert tf.keep == 10
    assert tf.times == [(23,59), (12,30)]

def test_WeeklyTimeframe():
    assert WeeklyTimeframe.required_config_settings() == ["weekly_keep", "weekly_times", "weekly_days"]
    tf = WeeklyTimeframe(10, [(23,59), (12,30)], ["monday", "tuesday"])
    assert tf.name == "weekly"
    assert tf.keep == 10
    assert tf.times == [(23,59), (12,30)]
    assert tf.weekdays == ["monday", "tuesday"]

def test_MonthlyTimeframe():
    assert MonthlyTimeframe.required_config_settings() == ["monthly_keep", "monthly_times", "monthly_days"]
    tf = MonthlyTimeframe(10, [(23,59), (12,30)], [1,2,23])
    assert tf.name == "monthly"
    assert tf.keep == 10
    assert tf.times == [(23,59), (12,30)]
    assert tf.monthdays == [1,2,23]

def test_YearlyTimeframe():
    assert YearlyTimeframe.required_config_settings() == ["yearly_keep", "yearly_times", "yearly_days"]
    tf = YearlyTimeframe(10, [(23,59), (12,30)], [1,2,365])
    assert tf.name == "yearly"
    assert tf.keep == 10
    assert tf.times == [(23,59), (12,30)]
    assert tf.yeardays == [1,2,365]

def test_tframe_types():
    assert Timeframe.tframe_types() == [FiveMinuteTimeframe, HourlyTimeframe, DailyTimeframe, WeeklyTimeframe, MonthlyTimeframe, YearlyTimeframe]
    assert Timeframe.tframe_types(names=True) == ["5minute", "hourly", "daily", "weekly", "monthly", "yearly"]

def test_valid_keep():
    assert Timeframe.valid_keep(0)
    assert Timeframe.valid_keep(1)
    assert Timeframe.valid_keep(100)
    assert not Timeframe.valid_keep(-1)
    assert not Timeframe.valid_keep(0.5)

def test_valid_weekday():
    assert Timeframe.valid_weekday("tuesday")
    assert not Timeframe.valid_weekday("foo")

def test_valid_monthday():
    assert Timeframe.valid_monthday(1)
    assert Timeframe.valid_monthday(5)
    assert Timeframe.valid_monthday(31)
    assert not Timeframe.valid_monthday(0)
    assert not Timeframe.valid_monthday(32)
    assert not Timeframe.valid_monthday(10.5)

def test_valid_yearday():
    assert Timeframe.valid_yearday(1)
    assert Timeframe.valid_yearday(5)
    assert Timeframe.valid_yearday(365)
    assert Timeframe.valid_yearday(366, leap=True)
    assert not Timeframe.valid_yearday(0)
    assert not Timeframe.valid_yearday(366)
    assert not Timeframe.valid_yearday(367, leap=True)
    assert not Timeframe.valid_yearday(10.5)

def test_valid_timespec():
    assert Timeframe.valid_timespec("23:59")
    assert Timeframe.valid_timespec("00:12")
    assert Timeframe.valid_timespec("12:30")
    assert Timeframe.valid_timespec("13:00")
    assert not Timeframe.valid_timespec("24:00")
    assert not Timeframe.valid_timespec("23:60")
    assert not Timeframe.valid_timespec("0:12")
    assert not Timeframe.valid_timespec("00:1")
    assert not Timeframe.valid_timespec("-01:12")
    assert not Timeframe.valid_timespec("01:-12")

def test_valid_minute():
    assert Timeframe.valid_minute(0)
    assert Timeframe.valid_minute(59)
    assert Timeframe.valid_minute(10)
    assert not Timeframe.valid_minute(60)
    assert not Timeframe.valid_minute(-1)
    assert not Timeframe.valid_minute(10.5)

def test_valid_hour():
    assert Timeframe.valid_hour(0)
    assert Timeframe.valid_hour(23)
    assert Timeframe.valid_hour(10)
    assert not Timeframe.valid_hour(24)
    assert not Timeframe.valid_hour(-1)
    assert not Timeframe.valid_hour(10.5)

def test_valid_time():
    assert Timeframe.valid_time((0,0))
    assert Timeframe.valid_time((23,59))
    assert Timeframe.valid_time((12,30))
    assert not Timeframe.valid_hour((-1,0))
    assert not Timeframe.valid_hour((0,60))

def test_timespec_to_time():
    assert (1, 2) == Timeframe.timespec_to_time("01:02")
    assert (0, 0) == Timeframe.timespec_to_time("00:00")
    assert (23, 59) == Timeframe.timespec_to_time("23:59")
