"""Transactional RAW INSERT repository with fixed table specifications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .raw_specs import RawTable, RawTableSpec, get_raw_spec


class RawRepositoryError(RuntimeError):
    """A collector row does not match its approved RAW table definition."""


@dataclass(frozen=True)
class RawWriteResult:
    table: str
    requested_count: int
    inserted_count: int
    duplicate_count: int

    def __post_init__(self) -> None:
        if self.requested_count != self.inserted_count + self.duplicate_count:
            raise ValueError("requested_count must equal inserted_count plus duplicate_count")


def _jsonb(value):
    try:
        from psycopg.types.json import Jsonb
    except ImportError as error:
        raise RawRepositoryError("psycopg is not installed. Install project requirements first.") from error
    return Jsonb(value)


class RawRepository:
    """Writes only known RAW tables; caller-controlled SQL identifiers are not accepted."""

    def __init__(self, pool, *, jsonb_factory=_jsonb) -> None:
        self.pool = pool
        self._jsonb_factory = jsonb_factory

    def save(self, table: RawTable, rows: Mapping[str, object] | Sequence[Mapping[str, object]]) -> RawWriteResult:
        if not isinstance(table, RawTable):
            raise RawRepositoryError("RAW table must be an approved RawTable value.")
        spec = get_raw_spec(table)
        normalized = self._normalize_rows(rows)
        if not normalized:
            return RawWriteResult(table.value, 0, 0, 0)
        values = [self._to_values(spec, row) for row in normalized]
        try:
            with self.pool.connection() as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(self._insert_sql(spec, len(values)), tuple(item for row in values for item in row))
                        inserted_count = len(cursor.fetchall())
        except RawRepositoryError:
            raise
        except Exception as error:
            raise RawRepositoryError(f"RAW insert failed for {table.value}.") from error
        return RawWriteResult(table.value, len(values), inserted_count, len(values) - inserted_count)

    @staticmethod
    def _normalize_rows(rows: Mapping[str, object] | Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
        if isinstance(rows, Mapping):
            return [rows]
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)) and all(isinstance(row, Mapping) for row in rows):
            return list(rows)
        raise RawRepositoryError("RAW rows must be a mapping or a sequence of mappings.")

    def _to_values(self, spec: RawTableSpec, row: Mapping[str, object]) -> tuple[object, ...]:
        actual, expected = set(row), set(spec.columns)
        if actual != expected:
            raise RawRepositoryError(f"RAW row fields do not match {spec.table.value}: missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}")
        return tuple(self._jsonb_factory(row[column]) if column == "raw_payload" else row[column] for column in spec.columns)

    @staticmethod
    def _insert_sql(spec: RawTableSpec, row_count: int) -> str:
        if row_count < 1:
            raise ValueError("row_count must be positive")
        values = ", ".join("(" + ", ".join("%s" for _ in spec.columns) + ")" for _ in range(row_count))
        return f"INSERT INTO {spec.table.value} (" + ", ".join(spec.columns) + f") VALUES {values} ON CONFLICT DO NOTHING RETURNING 1"
