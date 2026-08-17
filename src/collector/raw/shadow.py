"""Read-only comparison of a shadow record with canonical RAW."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Mapping

from src.repository.raw_specs import RawTable, get_raw_spec


IGNORED_COLUMNS = frozenset({"collected_at"})


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def semantic_equal(left, right) -> bool:
    """Compare JSON objects by value, never by key serialization order."""
    return json.dumps(_json_value(left), sort_keys=True, separators=(",", ":"), ensure_ascii=False) == json.dumps(
        _json_value(right), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


@dataclass(frozen=True)
class ShadowComparison:
    table: RawTable
    key: dict[str, object]
    matched: bool
    expected_found: bool
    mismatched_columns: tuple[str, ...]
    existing_collected_at: datetime | None = None


class RawShadowComparator:
    """Fetches an existing canonical row by the approved RAW primary key only."""

    def __init__(self, pool) -> None:
        self.pool = pool

    def compare(self, table: RawTable, actual: Mapping[str, object]) -> ShadowComparison:
        spec = get_raw_spec(table)
        key = {column: actual[column] for column in spec.primary_key}
        where = " AND ".join(f"{column}=%s" for column in spec.primary_key)
        columns = ", ".join(spec.columns)
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT {columns} FROM {table.value} WHERE {where}", tuple(key[column] for column in spec.primary_key))
            row = cursor.fetchone()
        if row is None:
            return ShadowComparison(table, key, False, False, tuple(), None)
        expected = dict(zip(spec.columns, row))
        mismatched = tuple(
            column for column in spec.columns
            if column not in IGNORED_COLUMNS and not semantic_equal(expected[column], actual[column])
        )
        return ShadowComparison(table, key, not mismatched, True, mismatched, expected.get("collected_at"))


def _session(value: datetime) -> str | None:
    moment = value.time()
    if (moment.hour, moment.minute) >= (8, 0) and (moment.hour, moment.minute) <= (8, 49):
        return "NXT_PREMARKET"
    if (moment.hour, moment.minute) >= (9, 0) and (moment.hour, moment.minute) <= (15, 19):
        return "KRX_REGULAR"
    if (moment.hour, moment.minute) >= (15, 40) and (moment.hour, moment.minute) <= (20, 0):
        return "NXT_AFTERMARKET"
    return None


def unexpected_minute_gaps(times: list[datetime]) -> list[tuple[datetime, datetime]]:
    """Return only same-date, same-session discontinuities.

    Auction/excluded windows and session boundaries are intentionally not gaps.
    The helper makes the shadow report's gap terminology match the established
    multi-market session contract without making a LIVE data-quality decision.
    """
    ordered = sorted(set(times))
    gaps: list[tuple[datetime, datetime]] = []
    for previous, current in zip(ordered, ordered[1:]):
        if previous.date() != current.date() or _session(previous) != _session(current) or _session(current) is None:
            continue
        if current - previous != timedelta(minutes=1):
            gaps.append((previous, current))
    return gaps
