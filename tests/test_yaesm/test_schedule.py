"""Tests for yaesm.schedule."""

from apscheduler.triggers.cron import CronTrigger

from yaesm.schedule import Schedule


def test_schedule():
    trigger = CronTrigger(minute=0)
    schedule = Schedule("hourly", (trigger,))

    assert schedule.name == "hourly"
    assert schedule.triggers == (trigger,)
