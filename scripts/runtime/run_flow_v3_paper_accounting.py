"""PAPER-only cost, variable-share compounding and reporting projection service."""
import argparse
import signal
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings, create_connection_pool
from src.flow_v3.accounting_repository import PaperAccountingRepository


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--batch-size', type=int, default=50)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 100:
        parser.error('batch-size must be 1..100')
    load_dotenv(ROOT / '.env')
    stop = threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        worker = PaperAccountingRepository(pool)
        while not stop.is_set():
            result = worker.run_batch(args.batch_size)
            if result['processed'] or result['blocked'] or args.once:
                print(result, flush=True)
            if args.once:
                break
            stop.wait(1 if result['processed'] else 5)
    finally:
        pool.close()


if __name__ == '__main__':
    main()
