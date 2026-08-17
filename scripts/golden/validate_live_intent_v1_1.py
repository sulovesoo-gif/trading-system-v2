"""Read-only Golden-period forward replay through the intent-only LIVE adapter."""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.golden.validate_core_v1_1 import definition, raw_rows  # RAW provider only; no Golden input
from src.live_intent import InMemoryLiveIntentStore, LiveStrategyAdapter, LiveStrategyInstance, MarketContext
from src.strategy_core import HistoricalDataProvider


def main() -> None:
    grouped = defaultdict(list)
    for stock, bar in raw_rows():
        grouped[stock].append(bar)
    provider = HistoricalDataProvider(grouped)
    instances = (
        LiveStrategyInstance("S1", definition(instance="SAMSUNG_S1_LONG_PULLBACK_WITHIN30_EOD", code="S1_OR_PULLBACK_RESTART", signal_stock="005930", signal_direction="LONG", execution_stock="0193W0").definition),
        LiveStrategyInstance("S2", definition(instance="SAMSUNG_S2_SHORT_FIXED30", code="S2_FAILED_OR_VWAP", signal_stock="005930", signal_direction="SHORT", execution_stock="0193L0").definition),
        LiveStrategyInstance("S3_3", definition(instance="HYNIX_S3_SHORT_3BAR", code="S3_VOLUME_CLIMAX_REVERSAL", signal_stock="000660", signal_direction="SHORT", execution_stock="0197X0").definition, entry_group="HYNIX_S3_SHARED"),
        LiveStrategyInstance("S3_5", definition(instance="HYNIX_S3_SHORT_5BAR", code="S3_VOLUME_CLIMAX_REVERSAL", signal_stock="000660", signal_direction="SHORT", execution_stock="0197X0").definition, entry_group="HYNIX_S3_SHARED"),
    )
    store = InMemoryLiveIntentStore()
    adapter = LiveStrategyAdapter(provider=provider, instances=instances, store=store)
    dates = sorted({bar.time.date().isoformat() for bar in grouped["005930"]} | {bar.time.date().isoformat() for bar in grouped["000660"]})
    # No historical calendar exception is injected: every accepted entry must
    # pass the generic completed-bar quality gate from its own RAW lookback.
    for day in dates:
        adapter.process_completed_day(day, context=MarketContext())
    first_count = len(store.intents)
    first_core_calls = dict(adapter.entry_core_calls_by_group)
    for day in dates:
        adapter.process_completed_day(day, context=MarketContext())
    counts = Counter((intent.strategy_instance_id, intent.intent_type.value) for intent in store.intents.values())
    result = {
        "entry_core_calls": adapter.entry_core_calls,
        "first_replay_entry_core_calls_by_group": first_core_calls,
        "first_replay_intents": first_count,
        "second_replay_new_intents": len(store.intents) - first_count,
        "counts": {f"{key[0]}:{key[1]}": value for key, value in sorted(counts.items())},
        "audit_events": Counter(event.event_type for event in store.audits),
    }
    print(result)


if __name__ == "__main__":
    main()
