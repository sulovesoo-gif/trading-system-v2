from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings
def main():
 load_dotenv(ROOT/'.env');s=DatabaseSettings.from_environment();import psycopg
 with psycopg.connect(**s.connection_kwargs()) as c,c.cursor() as q:
  q.execute("""SELECT count(*) FILTER(WHERE operation_status='LIVE'),count(*) FILTER(WHERE operation_status='PAPER'),count(*) FROM daily_strategy_operation o JOIN daily_strategy_master m USING(strategy_id) WHERE effective_to IS NULL AND m.strategy_role='CANONICAL' AND m.is_enabled='Y'""");ops=q.fetchone()
  q.execute("SELECT count(*),count(*) FILTER(WHERE epoch_initial_capital=strategy_compound_capital AND cumulative_net_realized_pnl=0),sum(epoch_initial_capital) FROM daily_strategy_compound_capital WHERE capital_epoch_no=1");caps=q.fetchone()
  q.execute("SELECT count(*) FILTER(WHERE approved_initial_capital=1000000),count(*) FILTER(WHERE approved_initial_capital=100000),count(*) FILTER(WHERE approved_initial_capital=30000) FROM daily_strategy_live_initial_capital_approval WHERE selection_batch_id='DAILY_MA_SEL_20260824_V1'");tiers=q.fetchone()
  q.execute("SELECT (SELECT count(*) FROM daily_strategy_live_capital_settlement),(SELECT count(*) FROM daily_strategy_live_broker_order_mapping),(SELECT count(*) FROM live_broker_order),(SELECT count(*) FROM live_broker_fill)");safe=q.fetchone()
 print(json.dumps({'canonical_current_operations':ops,'capital_epochs':caps,'tier_amounts':tiers,'settlement_mapping_orders_fills':safe},default=str))
if __name__=='__main__':main()
