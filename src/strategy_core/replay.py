"""Public strategy decision API and read-only historical replay orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contracts import SignalDecision
from .engine import fixed30_exit, generate_s1, generate_s2, generate_s3_shared, s1_exit, s3_exit
from .historical import HistoricalDataProvider, HistoricalExecutionAdapter, HistoricalTrade
from .registry import StrategyDefinition


@dataclass(frozen=True)
class StrategyCore:
    """The single public Decision API used by historical and future LIVE adapters."""

    definition: StrategyDefinition

    def entry_decisions(self, source_bars: Iterable) -> tuple[SignalDecision, ...]:
        if self.definition.strategy_code == "S1_OR_PULLBACK_RESTART":
            return generate_s1(self.definition, source_bars)
        if self.definition.strategy_code == "S2_FAILED_OR_VWAP":
            return generate_s2(self.definition, source_bars)
        if self.definition.strategy_code == "S3_VOLUME_CLIMAX_REVERSAL":
            return generate_s3_shared(self.definition, source_bars)
        raise ValueError(f"unsupported strategy code: {self.definition.strategy_code}")

    def exit_decision(self, entry: SignalDecision, source_bars: Iterable, execution_bars: Iterable | None = None) -> SignalDecision:
        if self.definition.strategy_code == "S1_OR_PULLBACK_RESTART":
            return s1_exit(self.definition, entry, source_bars)
        if self.definition.strategy_code == "S2_FAILED_OR_VWAP":
            return fixed30_exit(self.definition, entry)
        if self.definition.strategy_code == "S3_VOLUME_CLIMAX_REVERSAL":
            if execution_bars is None:
                raise ValueError("S3 exit requires execution completed bars")
            structure = 3 if self.definition.strategy_instance.endswith("3BAR") else 5
            return s3_exit(self.definition, entry, source_bars, execution_bars, structure_bars=structure)
        raise ValueError(f"unsupported strategy code: {self.definition.strategy_code}")


class HistoricalGoldenValidationAdapter:
    """RAW completed bars -> public Core decisions -> execution-mapped trade records.

    Golden files are deliberately absent from this class.  They are loaded only
    by an outer assertion layer after this adapter has generated its universe.
    """

    def __init__(self, provider: HistoricalDataProvider) -> None:
        self.provider = provider
        self.execution = HistoricalExecutionAdapter(provider)

    def replay(self, core: StrategyCore, trading_dates: Iterable[str]) -> tuple[HistoricalTrade, ...]:
        produced: list[HistoricalTrade] = []
        for trading_date in sorted(set(trading_dates)):
            source = self.provider.bars(core.definition.signal_stock_code, trading_date)
            execution = self.provider.bars(core.definition.execution_stock_code, trading_date)
            for entry in core.entry_decisions(source):
                exit_decision = core.exit_decision(entry, source, execution)
                if exit_decision.decision_type.value != "EXIT":
                    continue
                # An execution product without the exact target completed bar
                # cannot become a historical trade.  This is an adapter-level
                # matching outcome, not a Core signal suppression rule.
                try:
                    entry_bar = self.execution.entry_bar(entry)
                    exit_bar = self.execution.exit_bar(exit_decision)
                except LookupError:
                    continue
                produced.append(HistoricalTrade(
                    strategy_instance=core.definition.strategy_instance,
                    strategy_code=core.definition.strategy_code,
                    trade_date=trading_date,
                    signal_stock_code=core.definition.signal_stock_code,
                    signal_direction=core.definition.signal_direction,
                    execution_stock_code=core.definition.execution_stock_code,
                    execution_direction=core.definition.execution_direction,
                    signal_time=entry.signal_time,
                    entry_target_time=entry.target_time,
                    entry_execution_time=entry_bar.time,
                    exit_trigger_time=exit_decision.signal_time,
                    exit_execution_time=exit_bar.time,
                    raw_entry_price=self.execution.entry_price(entry_bar),
                    raw_exit_price=self.execution.exit_price(exit_bar, eod_uses_close=exit_decision.exit_reason == "EOD_1519"),
                    exit_reason=exit_decision.exit_reason or "",
                    shared_entry_group=(
                        f"HYNIX_S3_{trading_date.replace('-', '')}_{entry.signal_time:%H%M}"
                        if core.definition.strategy_code == "S3_VOLUME_CLIMAX_REVERSAL" else None
                    ),
                    reference_levels=entry.reference_levels,
                ))
        return tuple(produced)
