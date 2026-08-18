"""Read-only report of the parameter contract consumed by the historical master procedure."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from src.repository.database import DatabaseSettings


def main() -> int:
    import psycopg

    load_dotenv(ROOT / ".env")
    settings = DatabaseSettings.from_environment()
    queries = {
        "columns": """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name='research_strategy_master'
            ORDER BY ordinal_position
        """,
        "variants": """
            SELECT strategy_group, signal_stock_code, signal_direction,
                   entry_variant, exit_variant, entry_params, exit_params,
                   count(*) AS strategy_count
            FROM research_strategy_master
            WHERE enabled_research_yn='Y'
            GROUP BY strategy_group, signal_stock_code, signal_direction,
                     entry_variant, exit_variant, entry_params, exit_params
            ORDER BY strategy_group, signal_stock_code, signal_direction,
                     entry_variant, exit_variant
        """,
        "family_counts": """
            SELECT strategy_group, count(*) AS strategy_count
            FROM research_strategy_master
            GROUP BY strategy_group ORDER BY strategy_group
        """,
    }
    with psycopg.connect(**settings.connection_kwargs()) as conn, conn.cursor() as cur:
        for name, sql in queries.items():
            cur.execute(sql)
            cols = [item.name for item in cur.description]
            print(f"--- {name} ---")
            for row in cur.fetchall():
                print(json.dumps(dict(zip(cols, row)), default=str, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
