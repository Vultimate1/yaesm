class Timeframe:
    """Maintains a timeframe defined in a given config file."""

    def __init__(self, frame_type, keep, **kwargs):
        self.type = frame_type
        self.keep = keep
        type_init_pairings = {
            "5minute" : self.five_minute_init,
            "hourly" : self.hourly_init,
            "daily" : self.daily_init,
            "weekly" : self.weekly_init,
            "monthly" : self.monthly_init,
            "yearly" : self.yearly_init
        }

    def five_minute_init(self):
        pass

    def hourly_init(self):
        pass

    def daily_init(self):
        pass

    def weekly_init(self):
        pass

    def monthly_init(self):
        pass

    def yearly_init(self):
        pass