from freezegun import freeze_time
import pytest
from threading import Thread
import time
from datetime import timedelta

from yaesm.scheduler import Scheduler
from yaesm.timeframe import *

def test_check_for_expired(capsys):
    scheduler = Scheduler()
    with freeze_time("1999-05-13 23:58"):
        scheduler.add_timeframe(FiveMinuteTimeframe(1), lambda : print(1))
        scheduler.add_timeframe(HourlyTimeframe(1, [59]), lambda : print(2))
        scheduler.add_timeframe(DailyTimeframe(1, [(23,59)]), lambda : print(3))
        scheduler.add_timeframe(
            WeeklyTimeframe(1, [(0,1), (23,59)], ["thursday", "friday"]), lambda : print(4)
        )
        with freeze_time("1999-05-14 00:04"):
            scheduler_thread = Thread(target=scheduler.check_for_expired)
            scheduler_thread.start()
            while scheduler_thread.is_alive():
                if not scheduler_thread.is_alive():
                    scheduler_thread.join()

            actual_out = capsys.readouterr().out.splitlines()
            expected_out = ["2", "3", "4", "1", "4"]
            assert actual_out == expected_out

def test_sleep_until_next_timeframe():
    scheduler = Scheduler()
    with freeze_time("1999-05-13 23:58") as frozen_datetime:
        scheduler.add_timeframe(FiveMinuteTimeframe(1), lambda : print(1))
        scheduler_thread = Thread(target=scheduler.sleep_until_next_timeframe, daemon=True)
        scheduler_thread.start()

        frozen_datetime.tick(delta=timedelta(minutes=1))
        assert scheduler_thread.is_alive()

        frozen_datetime.tick(delta=timedelta(minutes=1))
        time.sleep(1)
        assert not scheduler_thread.is_alive()
        scheduler_thread.join()

        scheduler_thread = Thread(target=scheduler.sleep_until_next_timeframe)
        scheduler_thread.start()
        time.sleep(0.1)
        assert not scheduler_thread.is_alive()
        scheduler_thread.join()
