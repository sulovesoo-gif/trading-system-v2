"""Independent FLOW LIVE preparation/recovery worker. Order POST is unavailable."""
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
        transport=FlowTransport(repository,client,account)
        while not stop.is_set():
            try:
                polled=reader.poll(repository)
                # Read two quotes once per cycle; no broker order endpoint call.
                quote_error=None
                try:
                    quotes=reader.quotes()
                except Exception as exc:
                    quotes={}
                    quote_error=type(exc).__name__
                result=repository.cycle(kst_now(),quotes)
                result['post']=transport.run()  # Closed code + DB gates return 0.
                if quote_error:
                    repository.record_error('QUOTE:'+quote_error)
                    result['quote_error']=quote_error
                print(json.dumps(dict(result,polled=polled,send=False),default=str),flush=True)
            except Exception as exc:
                # Safe names only: no token/account or HTTP payload in journal.
                repository.record_error(type(exc).__name__)
                print(json.dumps(dict(error=type(exc).__name__,post=0)),flush=True)
                if args.once:
                    raise
            if args.once:
                break
            stop.wait(10 if 9<=kst_now().hour<16 else 300)
    finally:
        pool.close()


if __name__=='__main__':
    main()
