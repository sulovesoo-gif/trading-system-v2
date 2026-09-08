"""Install no-SEND candidate snapshots; never create live requests or promote operations."""
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings,create_connection_pool
from src.flow_v3.accounting import quantity
from src.service.flow_v3_dashboard_service import LIVE_CANDIDATES


def main():
    load_dotenv(ROOT/'.env')
    pool=create_connection_pool(DatabaseSettings.from_environment())
    today=datetime.now(ZoneInfo('Asia/Seoul')).date()
    try:
        with pool.connection() as c,c.transaction():
            c.execute("SELECT pg_advisory_xact_lock(hashtext('FLOW_V3_LIVE_PREPARATION'))")
            if c.execute('SELECT EXISTS(SELECT 1 FROM flow_v3_live_trade)').fetchone()[0]:
                raise RuntimeError('Existing LIVE trades require verified cost-finalized settlement integration')
            rows=c.execute('SELECT strategy_id,stock_code,direction,execution_code FROM flow_v3_strategy_master WHERE strategy_id=ANY(%s)',(list(LIVE_CANDIDATES),)).fetchall()
            if len(rows)!=15: raise RuntimeError('WHITELIST_COUNT_MISMATCH')
            day=c.execute("SELECT max(trade_date) FROM raw_stock_daily WHERE trading_venue='KRX' AND collect_cycle='DAILY' AND data_source='KIS' AND trade_date<%s",(today,)).fetchone()[0]
            for sid,stock,direction,code in rows:
                expected=('LONG','0193T0') if sid in LIVE_CANDIDATES[:11] else ('SHORT','0197X0')
                if stock!='000660' or (direction,code)!=expected: raise RuntimeError('MAPPING_MISMATCH:'+sid)
                price=c.execute("""SELECT close_price FROM raw_stock_daily WHERE stock_code=%s
                  AND trade_date=%s AND trading_venue='KRX' AND collect_cycle='DAILY' AND data_source='KIS'
                  AND close_price>0 ORDER BY collected_at DESC LIMIT 1""",(code,day)).fetchone()
                if price is None: raise RuntimeError('MISSING_PREVIOUS_KRX_CLOSE:'+code)
                close=price[0];capital=close*Decimal('1.5')
                # Close-based quantity is an indicative snapshot, NOT an executable order quote.
                c.execute("""INSERT INTO flow_v3_live_preparation
                  (strategy_id,stock_code,direction,execution_code,approval_reference,
                   initial_price_date,initial_close,initial_capital,current_capital,
                   reference_price,reference_price_time,next_quantity)
                  VALUES(%s,%s,%s,%s,'USER_APPROVED_FLOW_V3_15',%s,%s,%s,%s,%s,%s,%s)
                  ON CONFLICT(strategy_id) DO NOTHING""",
                  (sid,stock,direction,code,day,close,capital,capital,close,
                   None,quantity(capital,close)))
            print('NO_SEND candidate snapshots: '+str(c.execute('SELECT count(*) FROM flow_v3_live_preparation').fetchone()[0]))
    finally: pool.close()


if __name__=='__main__': main()
