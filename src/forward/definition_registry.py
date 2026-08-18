"""Frozen champion mapping from canonical LIVE registry to Strategy Core."""

from __future__ import annotations

from src.live_registry.registry import LiveStrategyRegistryRepository
from src.strategy_core.registry import StrategyDefinition
from src.research_core.registry import PostgresResearchMasterRegistry


class ForwardDefinitionRegistry:
    """Maps only the four FROZEN champion IDs; it does not invent strategies."""

    _CORE_CODES = {294: "S3_VOLUME_CLIMAX_REVERSAL", 299: "S3_VOLUME_CLIMAX_REVERSAL", 802: "S1_OR_PULLBACK_RESTART", 623: "S2_FAILED_OR_VWAP"}
    _CORE_INSTANCES = {294: "HYNIX_S3_SHORT_3BAR", 299: "HYNIX_S3_SHORT_5BAR", 802: "SAMSUNG_S1_LONG", 623: "SAMSUNG_S2_SHORT"}

    def __init__(self, connection_factory) -> None:
        self._live = LiveStrategyRegistryRepository(connection_factory)

    def resolve(self, strategy_reference: str) -> tuple[str, StrategyDefinition]:
        if strategy_reference.startswith("RESEARCH_STRATEGY_"):
            strategy_id = int(strategy_reference.removeprefix("RESEARCH_STRATEGY_"))
            definitions = PostgresResearchMasterRegistry(self._live._connection_factory).definitions(strategy_id=strategy_id)
            if len(definitions) != 1:
                raise ValueError("Research path must resolve exactly one enabled master row")
            return strategy_reference, definitions[0]
        matches = [item for item in self._live.resolve_canonical_live() if item.strategy_instance_id == strategy_reference]
        if len(matches) != 1:
            raise ValueError("forward candidate must reference exactly one canonical LIVE instance")
        item = matches[0]
        return item.strategy_instance_id, StrategyDefinition(
            strategy_id=item.strategy_id, strategy_code=self._CORE_CODES[item.strategy_id],
            strategy_instance=self._CORE_INSTANCES[item.strategy_id],
            signal_stock_code=item.signal_stock_code, signal_direction=item.signal_direction,
            execution_stock_code=item.execution_stock_code, execution_direction=item.execution_direction,
            entry_variant="FROZEN", exit_variant="FROZEN", entry_params={}, exit_params={},
            strategy_version="FROZEN_20260818",
        )
