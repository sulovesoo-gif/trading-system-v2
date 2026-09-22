"""Persistence limited to orderbook-condition research hits."""

from __future__ import annotations

from datetime import date, datetime
from uuid import NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb


class OrderbookConditionResearchRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    def record_hits(
        self, *, business_date: date, poll_time: datetime, condition_name: str,
        condition_seq: str, candidates,
    ) -> int:
        inserted = 0
        with self.pool.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            for candidate in candidates:
                hit_key = (
                    f"ORDERBOOK_CONDITION_HIT|{poll_time.isoformat()}|{condition_name}|"
                    f"{condition_seq}|{candidate.stock_code}"
                )
                cursor.execute(
                    """INSERT INTO orderbook_condition_research_hit
                       (condition_hit_id,business_date,poll_time,condition_name,condition_seq,
                        stock_code,stock_name,result_order,raw_payload)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(poll_time,condition_name,condition_seq,stock_code) DO NOTHING""",
                    (uuid5(NAMESPACE_URL, hit_key), business_date, poll_time, condition_name,
                     condition_seq, candidate.stock_code, candidate.stock_name,
                     candidate.result_rank, Jsonb(candidate.raw_payload)),
                )
                inserted += cursor.rowcount
        return inserted
