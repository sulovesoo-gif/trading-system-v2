"""Read-only PostgreSQL activity view for rollback-only research replay probes."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings

def main() -> int:
    import psycopg
    load_dotenv(ROOT / '.env')
    with psycopg.connect(**DatabaseSettings.from_environment().connection_kwargs()) as conn, conn.cursor() as cur:
        cur.execute("""SELECT pid,state,wait_event_type,wait_event,left(query,160)
                         FROM pg_stat_activity
                        WHERE datname=current_database() AND query ILIKE '%run_strategy_master_backtest%'
                        ORDER BY pid""")
        for row in cur.fetchall(): print('|'.join('' if value is None else str(value).replace('\n',' ') for value in row))
    return 0
if __name__ == '__main__': raise SystemExit(main())
