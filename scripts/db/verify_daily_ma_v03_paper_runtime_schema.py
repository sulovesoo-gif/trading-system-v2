"""Read-only operational verification for the V0.3 PAPER runtime schema."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.repository.database import DatabaseSettings


def main() -> int:
    load_dotenv(ROOT / ".env")
    settings = DatabaseSettings.from_environment()
    import psycopg
    with psycopg.connect(**settings.connection_kwargs()) as connection, connection.cursor() as cursor:
        cursor.execute("""SELECT relname FROM pg_class
                           WHERE relname=ANY(%s) AND relkind='r' ORDER BY relname""",
                       (["daily_strategy_trade_no_counter", "daily_strategy_paper_event",
                         "daily_strategy_paper_transition", "daily_strategy_paper_runtime_cursor"],))
        tables = [row[0] for row in cursor.fetchall()]
        cursor.execute("SELECT count(*) FROM vw_daily_strategy_v03_runtime")
        canonical = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM daily_strategy_paper_trade WHERE source_system='DAILY_MA_V03'")
        paper_rows = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM daily_strategy_paper_event")
        event_rows = cursor.fetchone()[0]
        cursor.execute("SELECT attr1 FROM common_code WHERE group_cd='SYSTEM_SWITCH' AND code='GLOBAL_TRADE_YN'")
        global_trade = cursor.fetchone()[0]
    print(json.dumps({"runtime_tables": tables, "canonical_runtime_strategies": canonical,
                      "daily_ma_v03_paper_rows": paper_rows, "paper_event_rows": event_rows,
                      "global_trade_yn": global_trade}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
