"""선물 과거 백필 계약 Manifest 조회 전용 Repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class FuturesBackfillManifest:
    manifest_id: int
    instrument_key: str
    market_division_code: str
    futures_code: str
    standard_code: str | None
    contract_name: str | None
    expiry_date: date | None
    valid_from: date
    valid_to: date
    evidence_status: str
    evidence_reference: str


class FuturesBackfillManifestRepository:
    """Manifest를 수정하지 않고 승인된 계약 구간만 조회한다."""

    def __init__(self, pool) -> None:
        self.pool = pool

    def active_for_range(self, *, instrument_key: str, start_date: date, end_date: date) -> list[FuturesBackfillManifest]:
        sql = (
            "SELECT manifest_id, instrument_key, market_division_code, futures_code, standard_code, "
            "contract_name, expiry_date, valid_from, valid_to, evidence_status, evidence_reference "
            "FROM futures_backfill_manifest "
            "WHERE instrument_key = %s AND valid_from <= %s AND valid_to >= %s "
            "ORDER BY valid_from, futures_code"
        )
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (instrument_key, end_date, start_date))
                return [FuturesBackfillManifest(*row) for row in cursor.fetchall()]

    def active_on(self, *, instrument_key: str, trade_date: date) -> list[FuturesBackfillManifest]:
        return [
            item
            for item in self.active_for_range(
                instrument_key=instrument_key,
                start_date=trade_date,
                end_date=trade_date,
            )
            if item.valid_from <= trade_date <= item.valid_to
        ]

    def by_futures_code(self, *, instrument_key: str, futures_code: str) -> FuturesBackfillManifest:
        sql = (
            "SELECT manifest_id, instrument_key, market_division_code, futures_code, standard_code, "
            "contract_name, expiry_date, valid_from, valid_to, evidence_status, evidence_reference "
            "FROM futures_backfill_manifest "
            "WHERE instrument_key = %s AND futures_code = %s "
            "ORDER BY valid_from LIMIT 1"
        )
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (instrument_key, futures_code))
                row = cursor.fetchone()
        if row is None:
            raise LookupError(f"선물 백필 Manifest가 없습니다: {instrument_key} / {futures_code}")
        return FuturesBackfillManifest(*row)
