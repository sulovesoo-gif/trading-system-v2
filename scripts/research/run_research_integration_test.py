"""Small, non-destructive integration smoke test for a preconfigured test DB."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from src.repository.database import DatabaseSettings, create_connection_pool
from src.repository.research_repository import ResearchRepository
from src.service.research_complete_replay_service import CompleteReplay
from src.analysis.feature.sma_feature import MinuteBar
from decimal import Decimal
from datetime import datetime, timedelta

def main():
    if os.getenv("DB_INTEGRATION_TEST") != "1" or "test" not in os.getenv("DB_NAME", "").lower():
        raise RuntimeError("requires explicit test DB")
    pool = create_connection_pool(DatabaseSettings.from_environment()); repo = ResearchRepository(pool)
    run_id = uuid4(); today = date(2026, 8, 3)
    try:
        repo.create_run(run_id=run_id, start_date=today, end_date=today, parameters={"smoke": True})
        values=[100,99,98,97,96,97,99,102,101,98,95,93,96,100,103]
        bars=[MinuteBar(datetime(2026,8,3,9)+timedelta(minutes=i),*(Decimal(value),)*4) for i,value in enumerate(values)]
        features, signals, cycles = CompleteReplay().run(bars)
        previous=None
        for feature in features:
            repo.save_feature(run_id=run_id, stock_code="TEST000", feature=feature, ma10_direction=CompleteReplay._ma10_direction(previous,feature)); previous=feature
        for signal in signals:
            repo.save_signal(run_id=run_id,stock_code="TEST000",strategy_code="SIGNAL_1",signal=signal,
                             ma10_direction=None,pending=False,confirm_time=None,session_code="TEST")
        cycle_id = None
        for cycle in cycles:
            cycle_id=repo.save_cycle(run_id=run_id,trade_stock_code="TEST000",signal_source_stock_code="TEST000",cycle=cycle)
            for leg in cycle.legs: repo.save_leg(cycle_id=cycle_id,leg=leg)
        if cycle_id is not None:
            repo.save_position_daily(run_id=run_id, cycle_id=cycle_id, trading_date=today,
                                    trade_stock_code="TEST000", signal_source_stock_code="TEST000",
                                    strategy_code="SIGNAL_1", observation_code="COMPLETE", direction="LONG",
                                    entry_date=today, entry_price=Decimal("100"), valuation_close_price=Decimal("101"),
                                    quantity=1, invested_amount=Decimal("100"), unrealized_profit=Decimal("1"),
                                    unrealized_return_rate=Decimal("1"), capital_return_rate=Decimal("0.00001"),
                                    position_status="OPEN")
        repo.rebuild_performance(run_id=run_id,start_date=today,end_date=today)
        print(f"run_id={run_id} features={len(features)} signals={len(signals)} cycles={len(cycles)} top={len(repo.top_period(run_id=run_id))}")
    finally:
        pool.close()
if __name__ == '__main__': main()
