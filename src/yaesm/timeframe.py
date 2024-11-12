class Timeframe:
    """Treat like a custom type for now."""
    def __init__(self, keep):
        self.keep = keep


class FiveMinutes(Timeframe):
    def __init__(self, keep):
        super().__init__(keep)


class Hourly(Timeframe):
    def __init__(self, keep, minutes):
        super().__init__(keep)
        self.minutes = minutes


class Daily(Timeframe):
    def __init__(self, keep, times):
        super().__init__(keep)
        self.times = times


class Weekly(Timeframe):
    def __init__(self, keep, days, times):
        super().__init__(keep)
        self.days = days
        self.times = times


class Monthly(Timeframe):
    def __init__(self, keep, days, times):
        super().__init__(keep)
        self.days = days
        self.times = times


class Yearly(Timeframe):
    def __init__(self, keep, days, times):
        super().__init__(keep)
        self.days = days
        self.times = times
