from datetime import datetime
import time


class Scheduler:
    """Takes a list of `Timeframe`s as input, and creates a backup
    for each expired timeframe. If none have expired, this object
    will sleep (block) until the next timeframe expires."""
    def __init__(self, timeframes):
        # Storing them like this to make it easier to check & update timeframes
        self.timeframe_iters = [
            [time_iter, timeframe_obj]
            for timeframe_obj in timeframes
            for time_iter in timeframe_obj.iters
        ]
    
    def check_and_sleep(self):
        """Checks if any timeframe has expired,
        (TODO: takes a backup if any have done so),
        then sleeps until the earliest timeframe expires.
        
        Warning: This function blocks the thread. Run it in a separete"""
        present = datetime.now()
        next_timeframe = None
        for time_iter, obj in self.timeframe_iters:
            if time_iter.get_next(datetime) <= present:
                # TODO Make a backup here!
                # TODO Check if we've exceeded the Timeframe's keep amount
                continue
            else:
                if time_iter.get_prev(datetime) < next_timeframe:
                    next_timeframe = time_iter
        time.sleep(next_timeframe.get_current(float))