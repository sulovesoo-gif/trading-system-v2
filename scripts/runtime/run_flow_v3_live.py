"""Independent FLOW LIVE worker. Broker POST requires FLOW ENV and DB approval."""
import argparse
import json
import signal
import sys
import threading
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings,create_connection_pool
from src.collector.raw.kis_client import KISClient
from src.collector.raw.kis_order_account import KISOrderAccount
from src.flow_v3.live_repository import LiveRepository
from src.flow_v3.live_broker import FlowBrokerReader,kst_now
from src.flow_v3.live_transport import FlowTransport
from src.flow_v3.preorder import FlowCashCheck
from src.flow_v3.live_scheduler import priority_exits,wait_seconds,eod_priority


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--activate-approved',action='store_true')
    args=parser.parse_args()
    load_dotenv(ROOT/'.env')
    pool=create_connection_pool(DatabaseSettings.from_environment())
    repository=LiveRepository(pool)
    stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):
        signal.signal(sig,lambda *_:stop.set())
    try:
        if args.activate_approved:
            repository.activate(kst_now())
        client=KISClient();account=KISOrderAccount.from_environment()
        reader=FlowBrokerReader(client,account)
        repository.cash_check=FlowCashCheck(client,account)
        transport=FlowTransport(repository,client,account)
        while not stop.is_set():
            try:
                critical=eod_priority(kst_now())
                priority=priority_exits(repository,reader,transport,kst_now)
                # No bulk historical polling or large BUY queue ahead of the
                # next EOD pass. A bounded BUY still permits the 15:18 entry.
                polled=0 if critical else reader.poll(repository)
                # Active routes plus old OPEN exposures retain their own quote axis.
                quote_error=None
                try:
                    quotes=reader.quotes(repository.quote_codes())
                except Exception as exc:
                    quotes={}
                    quote_error=type(exc).__name__
                result=repository.cycle(kst_now(),quotes,buy_budget=1 if critical else None)
                result['post']=transport.run(max_orders=1 if critical else 32)
                result.update(priority)
                if quote_error:
                    repository.record_error('QUOTE:'+quote_error)
                    result['quote_error']=quote_error
                print(json.dumps(dict(result,polled=polled),default=str),flush=True)
            except Exception as exc:
                # Safe names only: no token/account or HTTP payload in journal.
                repository.record_error(type(exc).__name__)
                print(json.dumps(dict(error=type(exc).__name__,post=0)),flush=True)
                if args.once:
                    raise
            if args.once:
                break
            stop.wait(wait_seconds(kst_now()))
    finally:
        pool.close()


if __name__=='__main__':
    main()
