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
    assert jobs[0].args[:4] == (backup, "hourly", config.backups, None)
    assert hasattr(jobs[0].args[4], "acquire")


def test_scheduler_ignores_on_demand_schedules():
    config, _backup = configured_backup(schedule=Schedule("manual", OnDemandSchedule()))

    scheduler = Scheduler(config)

    assert scheduler._scheduler.get_jobs() == []


def test_scheduler_jobs_for_same_backup_share_lock():
    backup = Backup(
        "home",
        DriverSource(mock.Mock()),
        mock.Mock(),
        schedules=(
            Schedule("hourly", CronSchedule("0 * * * *")),
            Schedule("daily", CronSchedule("0 0 * * *")),
            Schedule("manual", OnDemandSchedule()),
        ),
    )
    scheduler = Scheduler(Config({}, {"home": backup}))
    scheduler.enqueue_backup("home", "manual")

    jobs = scheduler._scheduler.get_jobs()

    assert len(jobs) == 3
    assert all(job.args[-1] is jobs[0].args[-1] for job in jobs)
    assert hasattr(jobs[0].args[-1], "acquire")


def test_scheduler_jobs_for_different_backups_use_different_locks():
    first_config, first = configured_backup("first")
    _second_config, second = configured_backup("second")
    jobs = Scheduler(Config({}, {"first": first, "second": second}))._scheduler.get_jobs()
    locks = {job.args[0].name: job.args[-1] for job in jobs}

    assert locks["first"] is not locks["second"]


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
    assert job.args[:4] == (backup, "manual", config.backups, request_id)
    assert hasattr(job.args[4], "acquire")


def test_scheduler_selects_on_demand_schedule():
    config, backup = configured_backup(schedule=Schedule("manual", OnDemandSchedule()))
    scheduler = Scheduler(config)

    request_id = scheduler.enqueue_backup("home")

    job = scheduler._scheduler.get_job(request_id)
    assert job is not None
    assert job.args[:4] == (backup, "manual", config.backups, request_id)


def test_scheduler_requires_on_demand_schedule():
    config, _backup = configured_backup()
    scheduler = Scheduler(config)

    with pytest.raises(SchedulerError, match="backup 'home' has no on-demand schedule"):
        scheduler.enqueue_backup("home")


def test_scheduler_rejects_timer_schedule():
    config, _backup = configured_backup()
    scheduler = Scheduler(config)

    with pytest.raises(SchedulerError, match="schedule 'hourly'.*is not on-demand"):
        scheduler.enqueue_backup("home", "hourly")


def test_scheduler_requires_explicit_name_for_multiple_on_demand_schedules():
    backup = Backup(
        "home",
        DriverSource(mock.Mock()),
        mock.Mock(),
        schedules=(
            Schedule("first", OnDemandSchedule()),
            Schedule("second", OnDemandSchedule()),
        ),
    )
    scheduler = Scheduler(Config({}, {"home": backup}))

    with pytest.raises(SchedulerError, match="backup 'home' has multiple on-demand schedules"):
        scheduler.enqueue_backup("home")


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


def test_scheduler_replaces_finished_timer_job():
    first, _first_backup = configured_backup("first")
    second, _second_backup = configured_backup("second")
    scheduler = Scheduler(first)
    scheduler._scheduler.remove_job("first:hourly:0")

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
    assert job.args[:4] == (second_backup, "manual", second.backups, request_id)


def test_scheduler_reload_preserves_queued_backup():
    first, _first_backup = configured_backup("first", Schedule("manual", OnDemandSchedule()))
    second, _second_backup = configured_backup("second")
    scheduler = Scheduler(first)
    request_id = scheduler.enqueue_backup("first", "manual")

    scheduler.replace_config(second)

    assert {job.id for job in scheduler._scheduler.get_jobs()} == {
        request_id,
        "second:hourly:0",
    }


def test_scheduler_reload_preserves_backup_lock():
    first, _first_backup = configured_backup("home", Schedule("manual", OnDemandSchedule()))
    second, _second_backup = configured_backup("home")
    scheduler = Scheduler(first)
    scheduler.enqueue_backup("home", "manual")

    scheduler.replace_config(second)

    jobs = scheduler._scheduler.get_jobs()
    assert len(jobs) == 2
    assert jobs[0].args[-1] is jobs[1].args[-1]


def test_scheduled_job_executes_backup(monkeypatch):
    config, backup = configured_backup()
    now = datetime(2026, 8, 28, 12, 30)
    execute = mock.Mock()
    monkeypatch.setattr(Backup, "execute", execute)
    monkeypatch.setattr(scheduler_module, "datetime", mock.Mock(now=lambda: now))

    backup_lock = mock.MagicMock()
    scheduler_module._execute_backup(
        backup,
        "hourly",
        config.backups,
        "request-id",
        backup_lock,
    )

    execute.assert_called_once_with("hourly", now, config.backups)
    assert backup_lock.mock_calls == [mock.call.__enter__(), mock.call.__exit__(None, None, None)]


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
