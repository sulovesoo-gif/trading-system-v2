"""Rollback-only TEST E2E for Daily MA V0.3 LIVE NO_SEND persistence."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.daily_ma_v03.live_nosend import DailyMaLiveNoSendRuntime
from src.daily_ma_v03.live_nosend_repository import PostgresDailyMaLiveNoSendStore
from src.repository.database import DatabaseSettings


def main() -> int:
    load_dotenv(ROOT / ".env")
    settings = DatabaseSettings.from_environment()
    if settings.name != "trading_system_v2_test":
        raise SystemExit("fixture is restricted to trading_system_v2_test")
    import psycopg
    with psycopg.connect(**settings.connection_kwargs()) as connection:
        connection.execute("BEGIN")
        with connection.cursor() as cursor:
            cursor.execute("""SELECT p.paper_trade_id,p.strategy_id,m.execution_code
                                FROM daily_strategy_paper_trade p JOIN daily_strategy_master m USING(strategy_id)
                               WHERE p.source_system='DAILY_MA_V03' ORDER BY p.paper_trade_id LIMIT 1""")
            row = cursor.fetchone()
        if row is None:
            raise SystemExit("Daily MA V0.3 PAPER fixture row is required")
        paper_trade_id, strategy_id, stock = int(row[0]), str(row[1]), str(row[2])
        class SharedTransaction:
            def __enter__(self):
                return connection
            def __exit__(self, *_):
                return False
        factory = SharedTransaction
        runtime = DailyMaLiveNoSendRuntime(store=PostgresDailyMaLiveNoSendStore(factory, commit=False))
        base = dict(paper_trade_id=paper_trade_id, strategy_id=strategy_id, execution_stock_code=stock,
                    strategy_instance_id=f"DAILY_MA_{strategy_id}", quantity=1,
                    reference_price=Decimal("100"), signal_time=datetime(2026, 8, 24, 15, 18),
                    execution_target_time=datetime(2026, 8, 24, 15, 19), operation_status="LIVE")
        first = runtime.plan_entry(**base, signal_event_key="ROLLBACK_E2E_A", reconciliation_healthy=True)
        restart = DailyMaLiveNoSendRuntime(store=PostgresDailyMaLiveNoSendStore(factory, commit=False))
        duplicate = restart.plan_entry(**base, signal_event_key="ROLLBACK_E2E_A", reconciliation_healthy=True)
        second = runtime.plan_entry(**base, signal_event_key="ROLLBACK_E2E_B", reconciliation_healthy=True)
        unhealthy = runtime.plan_entry(**base, signal_event_key="ROLLBACK_E2E_C", reconciliation_healthy=False)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM daily_strategy_live_order_intent WHERE signal_event_key LIKE 'ROLLBACK_E2E_%'")
            intents = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM daily_strategy_live_order_request WHERE request_key IN (SELECT request_key FROM daily_strategy_live_order_request WHERE detail='{}'::jsonb) AND request_status='NO_SEND_VALIDATED'")
            requests = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM daily_strategy_live_capital_reservation WHERE intent_id IN (SELECT intent_id FROM daily_strategy_live_order_intent WHERE signal_event_key LIKE 'ROLLBACK_E2E_%')")
            reservations = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM daily_strategy_live_broker_order_mapping")
            mapping = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM live_broker_order")
            broker_orders = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM live_broker_fill")
            fills = cursor.fetchone()[0]
        connection.rollback()
    print(json.dumps({"first": first[1], "restart_duplicate": duplicate[1], "second": second[1],
                      "unhealthy": unhealthy[1], "fixture_intents": intents, "fixture_reservations": reservations,
                      "broker_mapping": mapping, "broker_orders": broker_orders, "broker_fills": fills}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
