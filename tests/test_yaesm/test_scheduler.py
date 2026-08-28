"""Tests for yaesm.scheduler."""

from datetime import datetime
from unittest import mock

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

import yaesm.scheduler as scheduler_module
from yaesm.backup import Backup, DriverSource
from yaesm.config import Config
from yaesm.schedule import CronSchedule, OnDemandSchedule, Schedule
from yaesm.scheduler import Scheduler, SchedulerError


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
    assert jobs[0].args == (backup, "hourly", config.backups, None)


def test_scheduler_ignores_on_demand_schedules():
    config, _backup = configured_backup(schedule=Schedule("manual", OnDemandSchedule()))

    scheduler = Scheduler(config)

    assert scheduler._scheduler.get_jobs() == []


def test_scheduler_enqueues_backup(monkeypatch):
    config, backup = configured_backup(schedule=Schedule("manual", OnDemandSchedule()))
    monkeypatch.setattr(
        scheduler_module.uuid,
        "uuid4",
        mock.Mock(return_value=mock.Mock(hex="request-id")),
    )
    scheduler = Scheduler(config)

    request_id = scheduler.enqueue_backup("home", "manual")

    assert request_id == "request-id"
    job = scheduler._scheduler.get_job(request_id)
    assert job is not None
    assert job.id == request_id
    assert job.name == "home (manual)"
    assert isinstance(job.trigger, DateTrigger)
    assert job.args == (backup, "manual", config.backups, request_id)


def test_scheduler_rejects_unknown_backup():
    config, _backup = configured_backup()
    scheduler = Scheduler(config)

    with pytest.raises(SchedulerError, match="unknown backup: 'missing'"):
        scheduler.enqueue_backup("missing", "hourly")


def test_scheduler_rejects_unknown_schedule():
    config, _backup = configured_backup()
    scheduler = Scheduler(config)

    with pytest.raises(
        SchedulerError,
        match="backup 'home' has no schedule 'missing'",
    ):
        scheduler.enqueue_backup("home", "missing")


def test_scheduler_replaces_config():
    first, _first_backup = configured_backup("first")
    second, _second_backup = configured_backup("second")
    scheduler = Scheduler(first)

    scheduler.replace_config(second)

    assert [job.id for job in scheduler._scheduler.get_jobs()] == ["second:hourly:0"]


def test_scheduler_enqueues_from_replaced_config():
    first, _first_backup = configured_backup("first", Schedule("manual", OnDemandSchedule()))
    second, second_backup = configured_backup("second", Schedule("manual", OnDemandSchedule()))
    scheduler = Scheduler(first)

    scheduler.replace_config(second)
    request_id = scheduler.enqueue_backup("second", "manual")

    job = scheduler._scheduler.get_job(request_id)
    assert job is not None
    assert job.args == (second_backup, "manual", second.backups, request_id)


def test_scheduled_job_executes_backup(monkeypatch):
    config, backup = configured_backup()
    now = datetime(2026, 8, 28, 12, 30)
    execute = mock.Mock()
    monkeypatch.setattr(Backup, "execute", execute)
    monkeypatch.setattr(scheduler_module, "datetime", mock.Mock(now=lambda: now))

    scheduler_module._execute_backup(backup, "hourly", config.backups, "request-id")

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
