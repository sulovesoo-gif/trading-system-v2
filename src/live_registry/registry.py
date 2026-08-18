"""Durable LIVE-strategy registry resolution; intentionally no runtime or broker imports."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class LiveStrategyRegistryError(ValueError):
    """Raised when a durable registry row cannot resolve uniquely and safely."""


def strategy_instance_id_for(live_strategy_id: int) -> str:
    """Stable runtime attribution derived only from the durable registry PK."""
    if live_strategy_id <= 0:
        raise LiveStrategyRegistryError("live_strategy_id must be positive")
    return f"LIVE_STRATEGY_{live_strategy_id}"


@dataclass(frozen=True)
class LiveStrategyResolution:
    live_strategy_id: int
    strategy_instance_id: str
    strategy_id: int
    strategy_code: str
    live_name: str
    live_yn: str
    signal_stock_code: str
    signal_direction: str
    execution_stock_code: str
    execution_direction: str
    initial_live_capital: Decimal
    master_live_enabled_yn: str

    @property
    def smoke_safe(self) -> bool:
        """7C resolves an explicitly registered, but not generally live, strategy."""
        return self.live_yn == "N" and self.master_live_enabled_yn == "Y"


class LiveStrategyRegistryRepository:
    """Repository for explicit registry administration and phase-gated resolution.

    The caller owns connection lifecycle. This module never writes runtime,
    intent, order, broker, capital, or global-trade state.
    """

    def __init__(self, connection_factory) -> None:
        self._connection_factory = connection_factory

    def register(self, *, strategy_id: int, live_name: str, initial_live_capital: Decimal) -> LiveStrategyResolution:
        """Create a disabled (`live_yn=N`) durable registry row exactly once."""
        if strategy_id <= 0:
            raise LiveStrategyRegistryError("strategy_id must be positive")
        if not live_name or not live_name.strip():
            raise LiveStrategyRegistryError("live_name is required")
        if initial_live_capital <= 0:
            raise LiveStrategyRegistryError("initial_live_capital must be positive KRW")

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM research_strategy_master WHERE strategy_id=%s",
                (strategy_id,),
            )
            if cursor.fetchone() is None:
                raise LiveStrategyRegistryError("strategy_id does not exist")
            cursor.execute(
                "SELECT live_strategy_id FROM research_live_strategy WHERE live_name=%s",
                (live_name,),
            )
            if cursor.fetchone() is not None:
                raise LiveStrategyRegistryError("live_name already exists")
            cursor.execute(
                """INSERT INTO research_live_strategy
                    (strategy_id, live_name, initial_live_capital, live_yn)
                    VALUES (%s, %s, %s, 'N')
                    RETURNING live_strategy_id""",
                (strategy_id, live_name, initial_live_capital),
            )
            live_strategy_id = cursor.fetchone()[0]
            connection.commit()
        return self.resolve_by_id(live_strategy_id)

    def resolve_by_id(self, live_strategy_id: int) -> LiveStrategyResolution:
        if live_strategy_id <= 0:
            raise LiveStrategyRegistryError("live_strategy_id must be positive")
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT l.live_strategy_id, l.strategy_id, m.strategy_code,
                          l.live_name, l.live_yn, m.signal_stock_code,
                          m.signal_direction, m.execution_stock_code,
                          m.execution_direction, l.initial_live_capital,
                          m.enabled_live_yn
                   FROM research_live_strategy l
                   JOIN research_strategy_master m ON m.strategy_id = l.strategy_id
                   WHERE l.live_strategy_id=%s""",
                (live_strategy_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise LiveStrategyRegistryError("live_strategy_id does not exist")
        return self._resolution(row)

    def resolve_smoke_candidate(self, *, active_stock_code: str) -> LiveStrategyResolution:
        """Return exactly one disabled registry row matching the approved product."""
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT l.live_strategy_id, l.strategy_id, m.strategy_code,
                          l.live_name, l.live_yn, m.signal_stock_code,
                          m.signal_direction, m.execution_stock_code,
                          m.execution_direction, l.initial_live_capital,
                          m.enabled_live_yn
                   FROM research_live_strategy l
                   JOIN research_strategy_master m ON m.strategy_id = l.strategy_id
                   WHERE m.execution_stock_code=%s
                     AND l.live_yn='N'
                     AND m.enabled_live_yn='Y'
                   ORDER BY l.live_strategy_id""",
                (active_stock_code,),
            )
            rows = cursor.fetchall()
        if len(rows) != 1:
            raise LiveStrategyRegistryError(
                f"expected exactly one smoke registry row for {active_stock_code}; found {len(rows)}"
            )
        return self._resolution(rows[0])

    @staticmethod
    def _resolution(row) -> LiveStrategyResolution:
        return LiveStrategyResolution(
            live_strategy_id=int(row[0]),
            strategy_instance_id=strategy_instance_id_for(int(row[0])),
            strategy_id=int(row[1]),
            strategy_code=str(row[2]),
            live_name=str(row[3]),
            live_yn=str(row[4]).strip(),
            signal_stock_code=str(row[5]),
            signal_direction=str(row[6]),
            execution_stock_code=str(row[7]),
            execution_direction=str(row[8]),
            initial_live_capital=Decimal(row[9]),
            master_live_enabled_yn=str(row[10]).strip(),
        )
