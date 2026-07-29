import unittest

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
