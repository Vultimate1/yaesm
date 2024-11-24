class Timeframe:
    """Maintains a timeframe defined in a given config file.

    Given a valid timeframe type, the object will attempt to
    convert the arguments into a list of times for backups.

    `**kwargs` must contain any other information related to a
    timeframe from the yaml file (other than keep). This class
    assumes that all additional args for a timeframe have been 
    passed on init."""

    VALID_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    DAYS_IN_MONTH_NO_LEAP_YEAR = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31}

    def __init__(self, frame_type, keep, **kwargs):
        self.keep = keep
        self.frame_type = frame_type
        self.kwargs = kwargs

    def check_keep_limit(self):
        """Checks if we've exceeded the limit set by `Timeframe.keep`.
        Deletes the earliest backups until the amount is equal to the
        limit again."""
        # TODO
        pass
