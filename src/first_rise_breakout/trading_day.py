"""FIRST_RISE daily memoization of the existing KisTradingCalendar result."""
import logging

LOGGER=logging.getLogger(__name__)


class FirstRiseTradingDay:
    def __init__(self,calendar):
        self.calendar=calendar
        self.day=None
        self.open=False

    def __call__(self,at):
        if self.day!=at.date():
            day=at.date()
            try:
                opened=day in self.calendar.open_dates(day,day)
                self.day,self.open=day,opened
                LOGGER.info('FIRST_RISE_TRADING_DAY date=%s open=%s',self.day,self.open)
            except Exception:
                LOGGER.exception('FIRST_RISE_TRADING_DAY_ERROR date=%s new_work_blocked=true',day)
                return False  # Unknown is not a confirmed holiday; retry next cycle.
        return self.open
