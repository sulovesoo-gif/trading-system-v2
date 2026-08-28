"""Bridge unplanned minute-MA PAPER entries into shared durable NO_SEND requests."""
from __future__ import annotations

import argparse,json,sys
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
import psycopg

from src.broker.cash_lookup import KISBrokerAvailableCashLookup
from src.collector.raw.kis_client import KISClient
from src.collector.raw.kis_order_account import KISOrderAccount
from src.minute_ma.live_nosend import PostgresMinuteMaNoSendAdapter
from src.repository.database import DatabaseSettings


def main() -> int:
    parser=argparse.ArgumentParser();parser.add_argument("--limit",type=int,default=100)
    args=parser.parse_args();load_dotenv(ROOT/".env")
    settings=DatabaseSettings.from_environment();factory=lambda:psycopg.connect(**settings.connection_kwargs())
    lookup=KISBrokerAvailableCashLookup(client=KISClient(),account=KISOrderAccount.from_environment())
    adapter=PostgresMinuteMaNoSendAdapter(factory)
    with factory() as connection,connection.cursor() as cursor:
        cursor.execute("""SELECT p.minute_path_id,t.minute_paper_trade_id,t.entry_event_key,
                                 s.execution_code,t.entry_price,t.entry_signal_time
                            FROM minute_ma_paper_trade t
                            JOIN minute_ma_path p USING(minute_path_id)
                            JOIN minute_ma_strategy_master s USING(minute_strategy_id)
                            JOIN minute_ma_operation o USING(minute_path_id)
                            LEFT JOIN minute_ma_live_intent i
                              ON i.minute_path_id=p.minute_path_id AND i.minute_paper_trade_id=t.minute_paper_trade_id
                            LEFT JOIN minute_ma_live_entry_skip k
                              ON k.minute_path_id=p.minute_path_id AND k.minute_paper_trade_id=t.minute_paper_trade_id
                           WHERE t.trade_status='OPEN' AND o.effective_to IS NULL
                             AND o.operation_status='LIVE' AND i.intent_id IS NULL AND k.skip_id IS NULL
                           ORDER BY t.entry_signal_time,t.minute_paper_trade_id LIMIT %s""",(args.limit,))
        candidates=cursor.fetchall()
    statuses={}
    for path_id,trade_id,event_key,stock,price,event_time in candidates:
        cash=lookup.orderable_cash(stock_code=str(stock),order_price=Decimal(price))
        result=adapter.plan_entry(minute_path_id=int(path_id),minute_paper_trade_id=int(trade_id),
          signal_event_key=str(event_key),execution_stock_code=str(stock),reference_price=Decimal(price),
          available_cash=cash.amount,cash_includes_pending_reservations=cash.includes_pending_order_reservations,
          source_event_time=event_time)
        statuses[result.status]=statuses.get(result.status,0)+1
    with factory() as connection,connection.cursor() as cursor:
        cursor.execute("""SELECT l.minute_live_trade_id,s.execution_code,p.exit_price,p.exit_signal_time,p.exit_reason
                            FROM minute_ma_live_trade l
                            JOIN minute_ma_paper_trade p USING(minute_paper_trade_id)
                            JOIN minute_ma_path x ON x.minute_path_id=l.minute_path_id
                            JOIN minute_ma_strategy_master s USING(minute_strategy_id)
                            LEFT JOIN minute_ma_live_intent i
                              ON i.minute_live_trade_id=l.minute_live_trade_id AND i.intent_type='EXIT'
                           WHERE l.trade_status='OPEN' AND p.trade_status='CLOSED' AND i.intent_id IS NULL
                           ORDER BY p.exit_signal_time,l.minute_live_trade_id LIMIT %s""",(args.limit,))
        exit_candidates=cursor.fetchall()
    for trade_id,stock,price,event_time,reason in exit_candidates:
        result=adapter.plan_exit(minute_live_trade_id=int(trade_id),execution_stock_code=str(stock),
          reference_price=Decimal(price),source_event_time=event_time,exit_reason=str(reason))
        statuses[result.status]=statuses.get(result.status,0)+1
    print(json.dumps({"mode":"NO_SEND","entry_candidates":len(candidates),
                      "exit_candidates":len(exit_candidates),"statuses":statuses,"order_post":0},sort_keys=True))
    return 0


if __name__=="__main__":raise SystemExit(main())
