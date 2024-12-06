import pytest
import random

from yaesm.timeframe import Timeframe, FiveMinuteTimeframe, HourlyTimeframe, DailyTimeframe, WeeklyTimeframe, MonthlyTimeframe, YearlyTimeframe

@pytest.fixture
def random_timeframe_generator(random_timeframe_times_generator, random_timeframe_minutes_generator, random_timeframe_weekdays_generator, random_timeframe_monthdays_generator, random_timeframe_yeardays_generator):
    """Fixture for generating a random Timeframe."""
    def generator(tframe_type=None, keep=None, minutes=None, times=None, weekdays=None, monthdays=None, yeardays=None) -> Timeframe:
        tframe_type = random.choice(Timeframe.valid_tframe_types()) if tframe_type is None else tframe_type
        keep        = random.randint(0,10) if keep is None else keep
        minutes     = random_timeframe_minutes_generator(random.randint(0,5)) if minutes is None else minutes
        times       = random_timeframe_times_generator(random.randint(0,5)) if times is None else times
        weekdays    = random_timeframe_weekdays_generator(random.randint(0,3)) if weekdays is None else weekdays
        monthdays   = random_timeframe_monthdays_generator(random.randint(0,3)) if monthdays is None else monthdays
        yeardays    = random_timeframe_yeardays_generator(random.randint(0,3)) if yeardays is None else yeardays
        if tframe_type == FiveMinuteTimeframe:
            return FiveMinuteTimeframe(keep)
        elif tframe_type == HourlyTimeframe:
            return HourlyTimeframe(keep, minutes)
        elif tframe_type == DailyTimeframe:
            return DailyTimeframe(keep, times)
        elif tframe_type == WeeklyTimeframe:
            return WeeklyTimeframe(keep, times, weekdays)
        elif tframe_type == MonthlyTimeframe:
            return MonthlyTimeframe(keep, times, monthdays)
        elif tframe_type == YearlyTimeframe:
            return YearlyTimeframe(keep, times, yeardays)
    return generator

@pytest.fixture
def random_timeframes_generator(random_timeframe_generator):
    """Fixture for generating a list of random Timeframes. See the fixture
    random_timeframe_generator for more details."""
    def generator(num=3, **kwargs):
        timeframes = []
        tframe_types = random.sample(Timframe.valid_tframe_types, k=num)
        for tframe_type in tframe_types:
            timeframes.append(random_timeframe_generator(tframe_type=tframe_type, **kwargs))
        return timeframes
    return generator

@pytest.fixture
def random_timeframe_minutes_generator():
    """Fixture to generate a list of random minutes."""
    def generator(num=3):
        minutes = []
        for _ in range(num):
            minute = None
            while minute is None or minute in minutes:
                minute = random.randint(0,59)
            minutes.append(time)
        return minutes
    return generator

@pytest.fixture
def random_timeframe_timespecs_generator():
    """Fixture to generate a list of random timeframe timespecs."""
    def generator(num=3):
        timepsecs = []
        for _ in range(num):
            timespec = None
            while timespec is None or timespec in timespecs:
                hour = str(random.randint(0,23)).zfill(2)
                minute = str(random.randint(0,59)).zfill(2)
                timespec = f"{hour}:{minute}"
            timespecs.append(timespec)
        return timespecs
    return generator

@pytest.fixture
def random_timeframe_times_generator(random_timeframe_timespecs_generator):
    """Fixture to generate a list of random timeframe times."""
    def generator(num=3):
        timespecs = random_timeframe_timespecs_generator(num=num)
        times = list(map(lambda x: Timeframe.timespec_to_time(x), timespecs))
        return times
    return generator

@pytest.fixture
def random_timeframe_weekdays_generator():
    """Fixture to generate a list of random timeframe weekdays."""
    def generator(num=3):
        weekdays = random.sample(["monday","tuesday","wednesday","thursday","friday","saturday","sunday"], k=num)
        return weekdays
    return generator

@pytest.fixture
def random_timeframe_monthdays_generator():
    """Fixture to generate a list or random Timeframe monthdays."""
    def generator(num=3):
        monthdays = random.sample(list(range(1,32)), k=num)
        return monthdays
    return generator

@pytest.fixture
def random_timeframe_yeardays_generator():
    """Fixture to generate a list of random Timeframe yeardays."""
    def generator(num=3, include_leap=False):
        max_day = 366 if include_leap else 365
        yeardays = random.sample(list(range(1,max_day+1)), k=num)
        return yeardays
    return generator

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

def test_timespec_to_time():
    assert (1, 2) == Timeframe.timespec_to_time("01:02")
    assert (0, 0) == Timeframe.timespec_to_time("00:00")
    assert (23, 59) == Timeframe.timespec_to_time("23:59")
