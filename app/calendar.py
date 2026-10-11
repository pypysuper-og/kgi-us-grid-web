from datetime import datetime


class MarketCalendar:
    def __init__(self):
        import exchange_calendars as xcals

        self.calendar = xcals.get_calendar("XNYS", side="left")

    def is_open(self, now: datetime) -> bool:
        import pandas as pd

        # Holidays, DST and scheduled early closes are handled by the local calendar.
        return bool(self.calendar.is_open_on_minute(pd.Timestamp(now).floor("min"), ignore_breaks=True))
