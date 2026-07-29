from datetime import date
import unittest

from src.collector.raw.domestic_stock.holiday_calendar_collector import HolidayCalendarCollector
from src.service.kis_trading_calendar import KisTradingCalendar


class FakeHolidayClient:
    def __init__(self):
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        base = kwargs["params"]["BASS_DT"]
        if base == "20260527":
            return {"output": [
                {"bass_dt": "20260527", "opnd_yn": "Y"},
                {"bass_dt": "20260528", "opnd_yn": "N"},
            ]}
        return {"output": [
            {"bass_dt": "20260529", "opnd_yn": "Y"},
            {"bass_dt": "20260530", "opnd_yn": "N"},
        ]}


class KisTradingCalendarTest(unittest.TestCase):
    def test_chains_calendar_blocks_and_uses_open_days_only(self):
        client = FakeHolidayClient()
        calendar = KisTradingCalendar(
            HolidayCalendarCollector(client), call_interval_seconds=0, sleep=lambda _: None
        )
        result = calendar.open_dates(date(2026, 5, 27), date(2026, 5, 30))
        self.assertEqual(result, [date(2026, 5, 27), date(2026, 5, 29)])
        self.assertEqual(client.calls[0]["tr_id"], "CTCA0903R")
        self.assertEqual(client.calls[1]["params"]["BASS_DT"], "20260529")
