from __future__ import annotations

import unittest
from datetime import date

from src.repository.futures_backfill_manifest_repository import FuturesBackfillManifestRepository


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def execute(self, sql, values):
        self.executed.append((sql, values))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor

    def cursor(self):
        return self.cursor_value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Pool:
    def __init__(self, rows):
        self.cursor_value = Cursor(rows)

    def connection(self):
        return Connection(self.cursor_value)


def manifest_row(code="A01609", start=date(2026, 6, 11), end=date(2026, 7, 28)):
    return (
        1, "KOSPI200_FUTURES", "F", code,
        "KR4A01690002" if code == "A01609" else None,
        "F 202609" if code == "A01609" else None,
        None, start, end,
        "OFFICIAL_MASTER_VERIFIED" if code == "A01609" else "API_VERIFIED_UNCONFIRMED",
        "evidence",
    )


class FuturesBackfillManifestRepositoryTest(unittest.TestCase):
    def test_range_query_uses_contract_overlap(self):
        pool = Pool([manifest_row()])
        items = FuturesBackfillManifestRepository(pool).active_for_range(
            instrument_key="KOSPI200_FUTURES",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 12),
        )
        self.assertEqual(items[0].futures_code, "A01609")
        _, values = pool.cursor_value.executed[0]
        self.assertEqual(values, ("KOSPI200_FUTURES", date(2026, 6, 12), date(2026, 6, 1)))

    def test_active_on_allows_deliberate_rollover_overlap(self):
        pool = Pool([
            manifest_row("A01606", date(2026, 5, 27), date(2026, 6, 11)),
            manifest_row("A01609", date(2026, 6, 11), date(2026, 7, 28)),
        ])
        items = FuturesBackfillManifestRepository(pool).active_on(
            instrument_key="KOSPI200_FUTURES",
            trade_date=date(2026, 6, 11),
        )
        self.assertEqual([item.futures_code for item in items], ["A01606", "A01609"])

    def test_unconfirmed_manifest_keeps_contract_details_none(self):
        item = FuturesBackfillManifestRepository(Pool([
            manifest_row("A01606", date(2026, 5, 27), date(2026, 6, 11))
        ])).by_futures_code(instrument_key="KOSPI200_FUTURES", futures_code="A01606")
        self.assertIsNone(item.standard_code)
        self.assertIsNone(item.contract_name)
        self.assertIsNone(item.expiry_date)

    def test_missing_code_raises(self):
        with self.assertRaises(LookupError):
            FuturesBackfillManifestRepository(Pool([])).by_futures_code(
                instrument_key="KOSPI200_FUTURES", futures_code="A01606"
            )
