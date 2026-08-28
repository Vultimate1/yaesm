"""Tests for yaesm.schedule."""

import pytest
import voluptuous as vlp
from apscheduler.triggers.cron import CronTrigger

from yaesm.errors import YaesmValueError
from yaesm.schedule import CronSchedule, OnDemandSchedule, Schedule, ScheduleBase


class ScheduleWithoutName(ScheduleBase):
    @staticmethod
    def config_schema():
        return vlp.Schema({})


class ScheduleWithoutConfiguration(ScheduleBase):
    @classmethod
    def name(cls):
        return "missing-configuration"


class UntimedSchedule(ScheduleBase):
    @classmethod
    def name(cls):
        return "untimed"

    @staticmethod
    def config_schema():
        return vlp.Schema({})


def test_schedule():
    implementation = CronSchedule("0 * * * *")
    schedule = Schedule("hourly", implementation)

    assert schedule.name == "hourly"
    assert schedule.implementation is implementation
    assert len(schedule.timer_triggers()) == 1
    assert isinstance(schedule.timer_triggers()[0], CronTrigger)


def test_schedule_can_have_no_timer_triggers():
    schedule = Schedule("external", UntimedSchedule())

    assert schedule.timer_triggers() == ()


def test_on_demand_schedule_is_explicitly_triggered():
    schedule = OnDemandSchedule()

    assert schedule.name() == "on-demand"
    assert schedule.timer_triggers() == ()


def test_on_demand_schedule_config_schema_constructs_schedule():
    assert OnDemandSchedule(**OnDemandSchedule.config_schema()({})).timer_triggers() == ()


@pytest.mark.parametrize("config", [None, True, 1, "on-demand", [], {"unknown": True}])
def test_on_demand_schedule_config_schema_rejects_settings(config):
    with pytest.raises(vlp.Invalid):
        OnDemandSchedule.config_schema()(config)


def test_cron_schedule_name():
    assert CronSchedule.name() == "cron"


def test_cron_schedule_config_schema_constructs_schedule():
    config = CronSchedule.config_schema()({"expression": "0 4 * * *"})

    assert CronSchedule(**config) == CronSchedule("0 4 * * *")


def test_cron_schedule_config_schema_accepts_shorthand():
    assert CronSchedule.config_schema()("0 4 * * *") == {"expression": "0 4 * * *"}


@pytest.mark.parametrize("expression", [None, True, 1, [], {}])
def test_cron_schedule_config_schema_rejects_nonstring_expression(expression):
    with pytest.raises(vlp.Invalid, match="expression must be a string"):
        CronSchedule.config_schema()({"expression": expression})


@pytest.mark.parametrize("expression", ["", "0 * * *", "invalid"])
def test_cron_schedule_config_schema_rejects_invalid_expression(expression):
    with pytest.raises(vlp.Invalid, match="invalid cron expression"):
        CronSchedule.config_schema()({"expression": expression})


@pytest.mark.parametrize(
    "config",
    [None, [], "config", 1, {}, {"expression": "0 * * * *", "unknown": True}],
)
def test_cron_schedule_config_schema_rejects_invalid_structure(config):
    with pytest.raises(vlp.Invalid):
        CronSchedule.config_schema()(config)


def test_cron_schedule_rejects_invalid_expression():
    with pytest.raises(YaesmValueError, match="invalid cron expression"):
        CronSchedule("invalid")


def test_cron_schedule_rejects_nonstring_expression():
    with pytest.raises(YaesmValueError, match="invalid cron expression"):
        CronSchedule(None)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    "schedule_type",
    [ScheduleWithoutName, ScheduleWithoutConfiguration],
)
def test_schedule_contract_is_required(schedule_type):
    with pytest.raises(TypeError):
        schedule_type()
