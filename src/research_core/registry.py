"""Read-only adapter from ``research_strategy_master`` to Research Core."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.strategy_core.registry import StrategyDefinition, strategy_from_registry_row


class PostgresResearchMasterRegistry:
    """Load master rows without creating candidates or changing registry state."""

    _SQL = """
        SELECT strategy_id, strategy_code, strategy_group, signal_stock_code,
               signal_direction, execution_stock_code, execution_direction,
               entry_variant, exit_variant, entry_params, exit_params
          FROM research_strategy_master
         WHERE enabled_research_yn='Y'
    """

    def __init__(self, pool) -> None:
        self.pool = pool

    def definitions(self, *, strategy_id: int | None = None) -> tuple[StrategyDefinition, ...]:
        sql = self._SQL + (" AND strategy_id=%s" if strategy_id is not None else "") + " ORDER BY strategy_id"
        params: tuple[int, ...] = (strategy_id,) if strategy_id is not None else ()
        acquire = self.pool.connection if hasattr(self.pool, "connection") else self.pool
        with acquire() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            names = [item.name for item in cursor.description]
            rows = [dict(zip(names, row)) for row in cursor.fetchall()]
        return tuple(self._definition(row) for row in rows)

    @staticmethod
    def _definition(row: Mapping[str, Any]) -> StrategyDefinition:
        params = dict(row.get("entry_params") or {})
        # The procedure dispatches by this database field.  Preserve it in the
        # pure definition rather than guessing from a display strategy_code.
        params["strategy_group"] = str(row["strategy_group"])
        adapted = dict(row); adapted["entry_params"] = params
        return strategy_from_registry_row(adapted, strategy_instance=f"RESEARCH_STRATEGY_{row['strategy_id']}", strategy_version="HISTORICAL_MASTER_PROCEDURE")
