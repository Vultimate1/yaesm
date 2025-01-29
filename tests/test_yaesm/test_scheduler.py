from freezegun import freeze_time
import pytest
from threading import Thread

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
