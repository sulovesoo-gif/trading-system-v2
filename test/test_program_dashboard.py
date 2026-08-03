from datetime import datetime
import unittest

from scripts.dashboard.serve_multi_ma_dashboard import build_program_minute_series


class ProgramMinuteSeriesTest(unittest.TestCase):
    def row(self, minute, amount):
        at = datetime(2026, 8, 3, 10, minute)
        return {"minute_time": at, "source_snapshot_time": at, "cumulative_sell_amount": 1,
                "cumulative_buy_amount": 1, "cumulative_net_buy_amount": amount,
                "cumulative_net_buy_volume": 1, "api_net_buy_amount_change": 0}

    def test_uses_continuous_minutes_only(self):
        values = build_program_minute_series([self.row(1, 100), self.row(2, 130), self.row(4, 90)])
        self.assertEqual([v["status"] for v in values], ["SESSION_START", "NORMAL", "PROGRAM_DATA_GAP"])
        self.assertEqual(values[1]["minute_net_buy_amount"], 30.0)
        self.assertIsNone(values[2]["minute_net_buy_amount"])

    def test_session_boundary_never_differences(self):
        at0 = datetime(2026, 8, 3, 8, 49)
        rows = [{**self.row(49, 100), "minute_time": at0, "source_snapshot_time": at0}]
        at = datetime(2026, 8, 3, 9, 0)
        rows.append({**self.row(0, 200), "minute_time": at, "source_snapshot_time": at})
        values = build_program_minute_series(rows)
        self.assertEqual(values[-1]["status"], "SESSION_START")
        self.assertIsNone(values[-1]["minute_net_buy_amount"])
