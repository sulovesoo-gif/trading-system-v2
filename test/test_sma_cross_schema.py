import unittest
from pathlib import Path

from scripts.db.apply_sma_cross_alert_schema import ANALYSIS_DDL_FILES


class SmaCrossSchemaTest(unittest.TestCase):
    def test_all_sma_signal_tables_are_applied(self):
        self.assertEqual(
            ANALYSIS_DDL_FILES,
            (
                "18_analysis_sma_cross_signal.sql",
                "19_analysis_sma_cross_performance.sql",
                "20_analysis_signal_notification.sql",
                "21_analysis_sma_cross_related_bar.sql",
            ),
        )

    def test_confirmed_signal_keeps_candidate_and_actual_confirmation_fields(self):
        ddl = (Path(__file__).resolve().parents[1] / "database" / "ddl" / "18_analysis_sma_cross_signal.sql").read_text(encoding="utf-8")
        for column in ("confirmed_time", "confirmed_price", "confirmed_change_from_previous"):
            self.assertIn(column, ddl)
        self.assertIn("WHERE status = 'INITIAL_CONFIRMED'", ddl)
