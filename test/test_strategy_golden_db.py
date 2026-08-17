"""Optional read-only proof that the committed Golden equals its canonical DB export."""

from __future__ import annotations

import csv
import json
import os
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path


@unittest.skipUnless(os.getenv("STRATEGY_GOLDEN_VALIDATE_DB") == "1", "set STRATEGY_GOLDEN_VALIDATE_DB=1 for read-only canonical DB validation")
class GoldenDbConsistencyTest(unittest.TestCase):
    def test_db_matches_committed_csv(self):
        from src.repository.database import DatabaseSettings, create_connection_pool

        fixture = Path(__file__).resolve().parent / "fixtures" / "strategy_golden" / "strategy_golden_final_v1.0.0.csv"
        with fixture.open(encoding="utf-8-sig", newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
        pool = create_connection_pool(DatabaseSettings.from_environment())
        try:
            with pool.connection() as connection, connection.cursor() as cursor:
                cursor.execute("""SELECT strategy_instance, strategy_code, trade_date, signal_stock_code, signal_direction,
                    execution_stock_code, signal_time, entry_time, exit_time, raw_entry_price, raw_exit_price,
                    exit_reason, shared_entry_group, reference_detail FROM strategy_golden_final
                    ORDER BY strategy_instance, signal_time""")
                db_rows = cursor.fetchall()
        finally:
            pool.close()

        def render(value):
            if isinstance(value, (datetime, date)):
                return value.isoformat()
            if isinstance(value, Decimal):
                return format(value, ".2f")
            return value

        def key_from_csv(row):
            return (row["strategy_instance"], datetime.fromisoformat(row["signal_time"]).isoformat())

        def key_from_db(row):
            return (row[0], render(row[6]))

        self.assertEqual(len(csv_rows), 33)
        self.assertEqual(len(db_rows), 33)
        csv_map = {key_from_csv(row): row for row in csv_rows}
        db_map = {key_from_db(row): row for row in db_rows}
        self.assertEqual(set(csv_map), set(db_map))
        for key, db in db_map.items():
            csv_row = csv_map[key]
            self.assertEqual([render(value) for value in db[:13]], [
                csv_row["strategy_instance"], csv_row["strategy_code"], csv_row["trade_date"],
                csv_row["signal_stock_code"], csv_row["signal_direction"], csv_row["execution_stock_code"],
                datetime.fromisoformat(csv_row["signal_time"]).isoformat(), datetime.fromisoformat(csv_row["entry_time"]).isoformat(),
                datetime.fromisoformat(csv_row["exit_time"]).isoformat(), f"{float(csv_row['raw_entry_price']):.2f}",
                f"{float(csv_row['raw_exit_price']):.2f}", csv_row["exit_reason"], csv_row["shared_entry_group"] or None,
            ])
            self.assertEqual(json.dumps(db[13], sort_keys=True, ensure_ascii=False),
                             json.dumps(json.loads(csv_row["reference_detail_json"]), sort_keys=True, ensure_ascii=False))
