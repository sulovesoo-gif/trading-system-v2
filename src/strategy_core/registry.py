"""Pure adapter for a research_strategy_master row.

The adapter intentionally accepts a mapping.  It does not query a repository or
couple the Strategy Core to the current draft registry schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: int | None
    strategy_code: str
    strategy_instance: str
    signal_stock_code: str
    signal_direction: str
    execution_stock_code: str
    execution_direction: str
    entry_variant: str
    exit_variant: str
    entry_params: Mapping[str, Any]
    exit_params: Mapping[str, Any]
    strategy_version: str = "1.0.0"
    code_commit: str | None = None


def strategy_from_registry_row(row: Mapping[str, Any], *, strategy_instance: str | None = None,
                               strategy_version: str = "1.0.0", code_commit: str | None = None) -> StrategyDefinition:
    """Translate a preloaded registry row; database access belongs to an outer adapter."""
    return StrategyDefinition(
        strategy_id=row.get("strategy_id"),
        strategy_code=str(row["strategy_code"]),
        strategy_instance=strategy_instance or str(row["strategy_code"]),
        signal_stock_code=str(row["signal_stock_code"]),
        signal_direction=str(row["signal_direction"]),
        execution_stock_code=str(row["execution_stock_code"]),
        execution_direction=str(row["execution_direction"]),
        entry_variant=str(row["entry_variant"]),
        exit_variant=str(row["exit_variant"]),
        entry_params=dict(row.get("entry_params") or {}),
        exit_params=dict(row.get("exit_params") or {}),
        strategy_version=strategy_version,
        code_commit=code_commit,
    )
