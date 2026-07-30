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

    def test_rejected_signals_are_not_confirmed_baselines(self):
        from src.repository.sma_cross_signal_repository import SmaCrossSignalRepository

        repository = SmaCrossSignalRepository(pool=None)
        captured = {}
        repository._one = lambda sql, values, required=False: captured.update(sql=sql, values=values)  # type: ignore[method-assign]
        repository.latest_confirmed("000660")
        self.assertIn("status IN ('INITIAL_CONFIRMED', 'CONFIRMED')", captured["sql"])
        self.assertNotIn("'REJECTED'", captured["sql"])

    def test_same_signal_notification_cannot_be_created_twice(self):
        from src.repository.sma_cross_signal_repository import SmaCrossSignalRepository

        ddl = (Path(__file__).resolve().parents[1] / "database" / "ddl" / "20_analysis_signal_notification.sql").read_text(encoding="utf-8")
        self.assertIn("UNIQUE (signal_id, notification_type)", ddl)
        repository = SmaCrossSignalRepository(pool=None)
        captured = {}
        repository._execute = lambda sql, values, returning=False: captured.update(sql=sql, values=values, returning=returning)  # type: ignore[method-assign]
        repository.create_notification(signal_id=3, notification_type="CONFIRMED")
        self.assertIn("ON CONFLICT (signal_id, notification_type) DO NOTHING", captured["sql"])
