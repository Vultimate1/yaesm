import pytest

from datetime import datetime
from zoneinfo import ZoneInfo
import logging

import yaesm.scheduler
import yaesm.timeframe
import yaesm.logging

def test_add_job_5minute_timeframe():
    scheduler = yaesm.scheduler.Scheduler()
    timeframe = yaesm.timeframe.FiveMinuteTimeframe(keep=10)
    scheduler._add_job("foo-name", lambda: 1, timeframe)
    jobs = scheduler._apscheduler.get_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.name == "foo-name"
    start_time = datetime(1999, 1, 1, 12, 3, tzinfo=ZoneInfo("UTC"))
    expected_times = [
        datetime(1999, 1, 1, 12, 5,  tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 1, 12, 10, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 1, 12, 15, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 1, 12, 20, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 1, 12, 25, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 1, 12, 30, tzinfo=ZoneInfo("UTC"))
    ]
    next_time = job.trigger.get_next_fire_time(None, start_time)
    for expected in expected_times:
        assert next_time == expected
        next_time = job.trigger.get_next_fire_time(next_time, next_time)

def test_add_job_hourly_timeframe():
    scheduler = yaesm.scheduler.Scheduler()
    timeframe = yaesm.timeframe.HourlyTimeframe(keep=24, minutes=[0, 15, 30, 45])
    scheduler._add_job("foo-name", lambda: 1, timeframe)
    jobs = scheduler._apscheduler.get_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.name == "foo-name"
    start_time = datetime(1999, 1, 1, 12, 3, tzinfo=ZoneInfo("UTC"))
    expected_times = [
        datetime(1999, 1, 1, 12, 15, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 1, 12, 30, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 1, 12, 45, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 1, 13, 0,  tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 1, 13, 15, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 1, 13, 30, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 1, 13, 45, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 1, 14, 0,  tzinfo=ZoneInfo("UTC"))
    ]
    next_time = job.trigger.get_next_fire_time(None, start_time)
    for expected in expected_times:
        assert next_time == expected
        next_time = job.trigger.get_next_fire_time(next_time, next_time)

def test_add_job_daily_timeframe():
    scheduler = yaesm.scheduler.Scheduler()
    timeframe = yaesm.timeframe.DailyTimeframe(keep=7, times=[(9, 0), (17, 30)])
    scheduler._add_job("foo-name", lambda: 1, timeframe)
    jobs = scheduler._apscheduler.get_jobs()
    assert len(jobs) == 2
    
    # Test first job (9:00)
    job1 = jobs[0]
    assert job1.name == "foo-name"
    start_time = datetime(1999, 1, 1, 8, 0, tzinfo=ZoneInfo("UTC"))
    expected_times = [
        datetime(1999, 1, 1, 9, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 2, 9, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 3, 9, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 4, 9, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 5, 9, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 6, 9, 0, tzinfo=ZoneInfo("UTC"))
    ]
    next_time = job1.trigger.get_next_fire_time(None, start_time)
    for expected in expected_times:
        assert next_time == expected
        next_time = job1.trigger.get_next_fire_time(next_time, next_time)
    
    # Test second job (17:30)
    job2 = jobs[1]
    assert job2.name == "foo-name"
    start_time = datetime(1999, 1, 1, 8, 0, tzinfo=ZoneInfo("UTC"))
    expected_times = [
        datetime(1999, 1, 1, 17, 30, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 2, 17, 30, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 3, 17, 30, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 4, 17, 30, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 5, 17, 30, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 1, 6, 17, 30, tzinfo=ZoneInfo("UTC"))
    ]
    next_time = job2.trigger.get_next_fire_time(None, start_time)
    for expected in expected_times:
        assert next_time == expected
        next_time = job2.trigger.get_next_fire_time(next_time, next_time)

def test_add_job_weekly_timeframe():
    scheduler = yaesm.scheduler.Scheduler()
    timeframe = yaesm.timeframe.WeeklyTimeframe(keep=4, times=[(10, 0), (18, 30)], weekdays=["monday", "friday"])
    scheduler._add_job("foobar-name", lambda: 1, timeframe)
    jobs = scheduler._apscheduler.get_jobs()
    assert len(jobs) == 2  # 2 times, each with monday,friday in day_of_week
    
    # Test job 1: Monday and Friday at 10:00
    job1 = jobs[0]
    assert job1.name == "foobar-name"
    start_time = datetime(1999, 1, 3, 8, 0, tzinfo=ZoneInfo("UTC"))  # Sunday Jan 3, 1999
    expected_times = [
        datetime(1999, 1, 4, 10, 0, tzinfo=ZoneInfo("UTC")),   # Monday
        datetime(1999, 1, 8, 10, 0, tzinfo=ZoneInfo("UTC")),   # Friday
        datetime(1999, 1, 11, 10, 0, tzinfo=ZoneInfo("UTC")),  # Monday
        datetime(1999, 1, 15, 10, 0, tzinfo=ZoneInfo("UTC")),  # Friday
        datetime(1999, 1, 18, 10, 0, tzinfo=ZoneInfo("UTC")),  # Monday
        datetime(1999, 1, 22, 10, 0, tzinfo=ZoneInfo("UTC")),  # Friday
    ]
    next_time = job1.trigger.get_next_fire_time(None, start_time)
    for expected in expected_times:
        assert next_time == expected
        next_time = job1.trigger.get_next_fire_time(next_time, next_time)
    
    # Test job 2: Monday and Friday at 18:30
    job2 = jobs[1]
    assert job2.name == "foobar-name"
    start_time = datetime(1999, 1, 3, 8, 0, tzinfo=ZoneInfo("UTC"))
    expected_times = [
        datetime(1999, 1, 4, 18, 30, tzinfo=ZoneInfo("UTC")),  # Monday
        datetime(1999, 1, 8, 18, 30, tzinfo=ZoneInfo("UTC")),  # Friday
        datetime(1999, 1, 11, 18, 30, tzinfo=ZoneInfo("UTC")), # Monday
        datetime(1999, 1, 15, 18, 30, tzinfo=ZoneInfo("UTC")), # Friday
        datetime(1999, 1, 18, 18, 30, tzinfo=ZoneInfo("UTC")), # Monday
        datetime(1999, 1, 22, 18, 30, tzinfo=ZoneInfo("UTC")), # Friday
    ]
    next_time = job2.trigger.get_next_fire_time(None, start_time)
    for expected in expected_times:
        assert next_time == expected
        next_time = job2.trigger.get_next_fire_time(next_time, next_time)

def test_add_job_monthly_timeframe():
    scheduler = yaesm.scheduler.Scheduler()
    timeframe = yaesm.timeframe.MonthlyTimeframe(keep=12, times=[(9, 0), (21, 0)], monthdays=[1, 15])
    scheduler._add_job("foo-name", lambda: 1, timeframe)
    jobs = scheduler._apscheduler.get_jobs()
    assert len(jobs) == 4  # 2 monthdays * 2 times = 4 jobs
    
    # Test job 1: 1st of month at 9:00
    job1 = jobs[0]
    assert job1.name == "foo-name"
    start_time = datetime(1999, 1, 10, 8, 0, tzinfo=ZoneInfo("UTC"))
    expected_times = [
        datetime(1999, 2, 1, 9, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 3, 1, 9, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 4, 1, 9, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 5, 1, 9, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 6, 1, 9, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 7, 1, 9, 0, tzinfo=ZoneInfo("UTC")),
    ]
    next_time = job1.trigger.get_next_fire_time(None, start_time)
    for expected in expected_times:
        assert next_time == expected
        next_time = job1.trigger.get_next_fire_time(next_time, next_time)
    
    # Test job 2: 1st of month at 21:00
    job2 = jobs[1]
    assert job2.name == "foo-name"
    start_time = datetime(1999, 1, 10, 8, 0, tzinfo=ZoneInfo("UTC"))
    expected_times = [
        datetime(1999, 2, 1, 21, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 3, 1, 21, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 4, 1, 21, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 5, 1, 21, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 6, 1, 21, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 7, 1, 21, 0, tzinfo=ZoneInfo("UTC")),
    ]
    next_time = job2.trigger.get_next_fire_time(None, start_time)
    for expected in expected_times:
        assert next_time == expected
        next_time = job2.trigger.get_next_fire_time(next_time, next_time)
    
    # Test job 3: 15th of month at 9:00
    job3 = jobs[2]
    assert job3.name == "foo-name"
    start_time = datetime(1999, 1, 10, 8, 0, tzinfo=ZoneInfo("UTC"))
    expected_times = [
        datetime(1999, 1, 15, 9, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 2, 15, 9, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 3, 15, 9, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 4, 15, 9, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 5, 15, 9, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 6, 15, 9, 0, tzinfo=ZoneInfo("UTC")),
    ]
    next_time = job3.trigger.get_next_fire_time(None, start_time)
    for expected in expected_times:
        assert next_time == expected
        next_time = job3.trigger.get_next_fire_time(next_time, next_time)
    
    # Test job 4: 15th of month at 21:00
    job4 = jobs[3]
    assert job4.name == "foo-name"
    start_time = datetime(1999, 1, 10, 8, 0, tzinfo=ZoneInfo("UTC"))
    expected_times = [
        datetime(1999, 1, 15, 21, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 2, 15, 21, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 3, 15, 21, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 4, 15, 21, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 5, 15, 21, 0, tzinfo=ZoneInfo("UTC")),
        datetime(1999, 6, 15, 21, 0, tzinfo=ZoneInfo("UTC")),
    ]
    next_time = job4.trigger.get_next_fire_time(None, start_time)
    for expected in expected_times:
        assert next_time == expected
        next_time = job4.trigger.get_next_fire_time(next_time, next_time)

def test_add_job_yearly_timeframe():
    scheduler = yaesm.scheduler.Scheduler()
    # Yearday 1 = Jan 1, Yearday 32 = Feb 1, Yearday 365 = Dec 31
    timeframe = yaesm.timeframe.YearlyTimeframe(keep=5, times=[(0, 0), (12, 0)], yeardays=[1, 32, 365])
    scheduler._add_job("foo-name", lambda: 1, timeframe)
    jobs = scheduler._apscheduler.get_jobs()
    assert len(jobs) == 6  # 3 yeardays * 2 times = 6 jobs
    
    # Test job 1: Jan 1 at 0:00
    job1 = jobs[0]
    assert job1.name == "foo-name"
    start_time = datetime(1999, 1, 1, 8, 0, tzinfo=ZoneInfo("UTC"))
    expected_times = [
        datetime(2000, 1, 1, 0, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2001, 1, 1, 0, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2002, 1, 1, 0, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2003, 1, 1, 0, 0, tzinfo=ZoneInfo("UTC")),
    ]
    next_time = job1.trigger.get_next_fire_time(None, start_time)
    for expected in expected_times:
        assert next_time == expected
        next_time = job1.trigger.get_next_fire_time(next_time, next_time)
    
    # Test job 2: Jan 1 at 12:00
    job2 = jobs[1]
    assert job2.name == "foo-name"
    start_time = datetime(1999, 1, 1, 8, 0, tzinfo=ZoneInfo("UTC"))
    expected_times = [
        datetime(1999, 1, 1, 12, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2000, 1, 1, 12, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2001, 1, 1, 12, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2002, 1, 1, 12, 0, tzinfo=ZoneInfo("UTC")),
    ]
    next_time = job2.trigger.get_next_fire_time(None, start_time)
    for expected in expected_times:
        assert next_time == expected
        next_time = job2.trigger.get_next_fire_time(next_time, next_time)
    
    # Test job 3: Feb 1 at 0:00
    job3 = jobs[2]
    assert job3.name == "foo-name"
    start_time = datetime(1999, 1, 1, 8, 0, tzinfo=ZoneInfo("UTC"))
    expected_times = [
        datetime(1999, 2, 1, 0, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2000, 2, 1, 0, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2001, 2, 1, 0, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2002, 2, 1, 0, 0, tzinfo=ZoneInfo("UTC")),
    ]
    next_time = job3.trigger.get_next_fire_time(None, start_time)
    for expected in expected_times:
        assert next_time == expected
        next_time = job3.trigger.get_next_fire_time(next_time, next_time)
    
    # Test job 4: Feb 1 at 12:00
    job4 = jobs[3]
    assert job4.name == "foo-name"
    start_time = datetime(1999, 1, 1, 8, 0, tzinfo=ZoneInfo("UTC"))
    expected_times = [
        datetime(1999, 2, 1, 12, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2000, 2, 1, 12, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2001, 2, 1, 12, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2002, 2, 1, 12, 0, tzinfo=ZoneInfo("UTC")),
    ]
    next_time = job4.trigger.get_next_fire_time(None, start_time)
    for expected in expected_times:
        assert next_time == expected
        next_time = job4.trigger.get_next_fire_time(next_time, next_time)
    
    # Test job 5: Dec 31 at 0:00
    job5 = jobs[4]
    assert job5.name == "foo-name"
    start_time = datetime(1999, 1, 1, 8, 0, tzinfo=ZoneInfo("UTC"))
    expected_times = [
        datetime(1999, 12, 31, 0, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2000, 12, 31, 0, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2001, 12, 31, 0, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2002, 12, 31, 0, 0, tzinfo=ZoneInfo("UTC")),
    ]
    next_time = job5.trigger.get_next_fire_time(None, start_time)
    for expected in expected_times:
        assert next_time == expected
        next_time = job5.trigger.get_next_fire_time(next_time, next_time)
    
    # Test job 6: Dec 31 at 12:00
    job6 = jobs[5]
    assert job6.name == "foo-name"
    start_time = datetime(1999, 1, 1, 8, 0, tzinfo=ZoneInfo("UTC"))
    expected_times = [
        datetime(1999, 12, 31, 12, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2000, 12, 31, 12, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2001, 12, 31, 12, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2002, 12, 31, 12, 0, tzinfo=ZoneInfo("UTC")),
    ]
    next_time = job6.trigger.get_next_fire_time(None, start_time)
    for expected in expected_times:
        assert next_time == expected
        next_time = job6.trigger.get_next_fire_time(next_time, next_time)

def test_add_backups_single_backup_single_timeframe(random_backup):
    scheduler = yaesm.scheduler.Scheduler()
    
    random_backup.timeframes = [yaesm.timeframe.FiveMinuteTimeframe(keep=10)]

    scheduler.add_backups([random_backup])
    jobs = scheduler._apscheduler.get_jobs()
    assert len(jobs) == 1
    assert jobs[0].name == f"{random_backup.name} (5minute)"

def test_add_backups_single_backup_multiple_timeframes(random_backup):
    scheduler = yaesm.scheduler.Scheduler()
    
    random_backup.timeframes = [
        yaesm.timeframe.FiveMinuteTimeframe(keep=10),
        yaesm.timeframe.HourlyTimeframe(keep=24, minutes=[0, 30]),
        yaesm.timeframe.DailyTimeframe(keep=7, times=[(9, 0)])
    ]

    scheduler.add_backups([random_backup])
    jobs = scheduler._apscheduler.get_jobs()
    # 5minute: 1, hourly: 1, daily: 1 = 3 total
    assert len(jobs) == 3

def test_add_backups_multiple_backups(random_backup_generator):
    scheduler = yaesm.scheduler.Scheduler()
    
    mock_backup1 = random_backup_generator()
    mock_backup1.timeframes = [yaesm.timeframe.FiveMinuteTimeframe(keep=10)]

    mock_backup2 = random_backup_generator()
    mock_backup2.timeframes = [yaesm.timeframe.HourlyTimeframe(keep=24, minutes=[0])]

    scheduler.add_backups([mock_backup1, mock_backup2])
    jobs = scheduler._apscheduler.get_jobs()
    assert len(jobs) == 2

def test_add_backups_multiple_backups_multiple_timeframes(random_backup_generator):
    scheduler = yaesm.scheduler.Scheduler()
    
    mock_backup1 = random_backup_generator()
    mock_backup1.timeframes = [
        yaesm.timeframe.FiveMinuteTimeframe(keep=10),
        yaesm.timeframe.DailyTimeframe(keep=7, times=[(9, 0), (17, 0)])
    ]

    mock_backup2 = random_backup_generator()
    mock_backup2.timeframes = [
        yaesm.timeframe.HourlyTimeframe(keep=24, minutes=[0, 30]),
        yaesm.timeframe.WeeklyTimeframe(keep=4, times=[(10, 0)], weekdays=["monday"])
    ]

    scheduler.add_backups([mock_backup1, mock_backup2])
    jobs = scheduler._apscheduler.get_jobs()
    # backup1: 5minute(1) + daily(2) = 3
    # backup2: hourly(1) + weekly(1) = 2
    # total = 5
    assert len(jobs) == 5

def test_add_backups_empty_list():
    scheduler = yaesm.scheduler.Scheduler()
    scheduler.add_backups([])
    jobs = scheduler._apscheduler.get_jobs()
    assert len(jobs) == 0

def test_job_fail_logs_instead_of_crashes(caplog):
    scheduler = yaesm.scheduler.Scheduler()
    call_count = [0]
    def fail_func():
        call_count[0] += 1
        if call_count[0] <= 3:
            raise Exception("TEST EXCEPTION")
        else:
            scheduler.stop(force=True)
    # Schedule a job to run immediately and repeatedly
    from datetime import datetime, timedelta
    start_date = datetime.now() + timedelta(seconds=0.5)
    scheduler._apscheduler.add_job(
        fail_func,
        'interval',
        seconds=0.3,
        start_date=start_date
    )
    # Start the scheduler (it will block until fail_func stops it)
    scheduler.start()
    assert call_count[0] == 4
    assert "TEST EXCEPTION" in caplog.text
