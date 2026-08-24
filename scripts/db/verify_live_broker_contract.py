"""Read-only TEST verification for the shared DDL 31 broker ledger."""

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
        cursor.execute("""SELECT to_regclass('public.live_broker_order'),
                                 to_regclass('public.live_broker_fill'),
                                 to_regclass('public.live_broker_order_audit'),
                                 to_regclass('public.execution_fill_allocation'),
                                 to_regclass('public.execution_logical_position'),
                                 (SELECT attr1 FROM common_code
                                   WHERE group_cd='SYSTEM_SWITCH' AND code='GLOBAL_TRADE_YN')""")
        tables = cursor.fetchone()
        if tables[0] is None:
            counts = {"live_broker_order": None, "live_broker_fill": None, "live_broker_order_audit": None}
        else:
            cursor.execute("""SELECT (SELECT count(*) FROM live_broker_order),
                                     (SELECT count(*) FROM live_broker_fill),
                                     (SELECT count(*) FROM live_broker_order_audit)""")
            row = cursor.fetchone()
            counts = {"live_broker_order": row[0], "live_broker_fill": row[1], "live_broker_order_audit": row[2]}
    print(json.dumps({"tables": {"live_broker_order": tables[0], "live_broker_fill": tables[1],
                                  "live_broker_order_audit": tables[2],
                                  "execution_fill_allocation": tables[3],
                                  "execution_logical_position": tables[4]},
                      "global_trade_yn": tables[5], "counts": counts}, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
