"""Terminate only explicitly listed rollback-only Research procedure probes."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings

def main() -> int:
    import psycopg
    parser = argparse.ArgumentParser(); parser.add_argument('pid', type=int, nargs='+'); args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    with psycopg.connect(**DatabaseSettings.from_environment().connection_kwargs()) as conn, conn.cursor() as cur:
        for pid in args.pid:
            cur.execute("SELECT query FROM pg_stat_activity WHERE pid=%s", (pid,)); row = cur.fetchone()
            if row is None or not str(row[0]).startswith('CALL run_strategy_master_backtest'):
                raise SystemExit(f'PID {pid} is not an explicitly identified Research replay probe')
            cur.execute('SELECT pg_terminate_backend(%s)', (pid,)); print(f'{pid}|{cur.fetchone()[0]}')
        conn.commit()
    return 0
if __name__ == '__main__': raise SystemExit(main())
