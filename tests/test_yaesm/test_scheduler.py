"""Tests for yaesm.scheduler."""

from datetime import datetime
from unittest import mock

from apscheduler.triggers.cron import CronTrigger

import yaesm.scheduler as scheduler_module
from yaesm.backup import Backup, DriverSource
from yaesm.config import Config
from yaesm.schedule import CronSchedule, Schedule, ScheduleBase
from yaesm.scheduler import Scheduler


class UntimedSchedule(ScheduleBase):
    @classmethod
    def name(cls) -> str:
        return "untimed"

    @staticmethod
    def config_schema():
        raise NotImplementedError


def configured_backup(
    name: str = "home",
    schedule: Schedule | None = None,
) -> tuple[Config, Backup]:
    schedule = schedule or Schedule("hourly", CronSchedule("0 * * * *"))
    backup = Backup(
        name,
        DriverSource(mock.Mock()),
        mock.Mock(),
        schedules=(schedule,),
    )
    return Config({}, {name: backup}), backup


def test_scheduler_adds_timer_jobs():
    config, backup = configured_backup()

    scheduler = Scheduler(config)
    jobs = scheduler._scheduler.get_jobs()

    assert len(jobs) == 1
    assert jobs[0].id == "home:hourly:0"
    assert jobs[0].name == "home (hourly)"
    assert isinstance(jobs[0].trigger, CronTrigger)
    assert jobs[0].args == (backup, "hourly", config.backups)


def test_scheduler_ignores_untimed_schedules():
    config, _backup = configured_backup(schedule=Schedule("external", UntimedSchedule()))

    scheduler = Scheduler(config)

    assert scheduler._scheduler.get_jobs() == []


def test_scheduler_replaces_config():
    first, _first_backup = configured_backup("first")
    second, _second_backup = configured_backup("second")
    scheduler = Scheduler(first)

    scheduler.replace_config(second)

    assert [job.id for job in scheduler._scheduler.get_jobs()] == ["second:hourly:0"]


def test_scheduled_job_executes_backup(monkeypatch):
    config, backup = configured_backup()
    now = datetime(2026, 8, 28, 12, 30)
    execute = mock.Mock()
    monkeypatch.setattr(Backup, "execute", execute)
    monkeypatch.setattr(scheduler_module, "datetime", mock.Mock(now=lambda: now))

    scheduler_module._execute_backup(backup, "hourly", config.backups)

    execute.assert_called_once_with("hourly", now, config.backups)


def test_scheduler_start_and_stop_delegate():
    config, _backup = configured_backup()
    scheduler = Scheduler(config)
    implementation = mock.Mock()
    scheduler._scheduler = implementation

    implementation.running = False
    scheduler.start()
    implementation.start.assert_called_once_with()

    implementation.running = True
    scheduler.stop()
    implementation.shutdown.assert_called_once_with(wait=False)
