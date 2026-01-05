import pytest

from yaesm.timeframe import Timeframe, FiveMinuteTimeframe, HourlyTimeframe, DailyTimeframe, \
    WeeklyTimeframe, MonthlyTimeframe, YearlyTimeframe

def test_FiveMinuteTimeframe():
    tf = FiveMinuteTimeframe(10)
    assert tf.name == "5minute"
    assert tf.keep == 10

def test_HourlyTimeframe():
    tf = HourlyTimeframe(10, [0,1,30,59])
    assert tf.name == "hourly"
    assert tf.keep == 10
    assert tf.minutes == [0,1,30,59]

def test_DailyTimeframe():
    tf = DailyTimeframe(10, [(23,59), (12,30)])
    assert tf.name == "daily"
    assert tf.keep == 10
    assert tf.times == [(23,59), (12,30)]

def test_WeeklyTimeframe():
    tf = WeeklyTimeframe(10, [(23,59), (12,30)], ["monday", "tuesday"])
    assert tf.name == "weekly"
    assert tf.keep == 10
    assert tf.times == [(23,59), (12,30)]
    assert tf.weekdays == ["monday", "tuesday"]

def test_MonthlyTimeframe():
    tf = MonthlyTimeframe(10, [(23,59), (12,30)], [1,2,23])
    assert tf.name == "monthly"
    assert tf.keep == 10
    assert tf.times == [(23,59), (12,30)]
    assert tf.monthdays == [1,2,23]

def test_YearlyTimeframe():
    tf = YearlyTimeframe(10, [(23,59), (12,30)], [1,2,365])
    assert tf.name == "yearly"
    assert tf.keep == 10
    assert tf.times == [(23,59), (12,30)]
    assert tf.yeardays == [1,2,365]

def test_tframe_types():
    assert Timeframe.tframe_types() == [FiveMinuteTimeframe, HourlyTimeframe, DailyTimeframe,
                              WeeklyTimeframe, MonthlyTimeframe, YearlyTimeframe]
    assert tframe_types(names=True) == ["5minute", "hourly", "daily", "weekly", "monthly",
                                        "yearly"]
