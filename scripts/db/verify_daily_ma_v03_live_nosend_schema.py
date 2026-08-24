"""Read-only TEST verification for Daily MA V0.3 LIVE NO_SEND persistence."""

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
        cursor.execute("""SELECT count(*) FILTER (WHERE trade_status NOT IN ('OPEN','CLOSED','CANCELLED')),
                                 count(*) FILTER (WHERE trade_status='OPEN'
                                                    AND lifecycle_status IN ('PLANNED','ENTRY_PENDING'))
                            FROM daily_strategy_live_trade""")
        invalid_status, premature_open = cursor.fetchone()
        cursor.execute("""SELECT (SELECT count(*) FROM daily_strategy_live_order_intent),
                                 (SELECT count(*) FROM daily_strategy_live_order_request),
                                 (SELECT count(*) FROM daily_strategy_live_broker_order_mapping),
                                 (SELECT count(*) FROM daily_strategy_live_capital_reservation),
                                 (SELECT attr1 FROM common_code
                                   WHERE group_cd='SYSTEM_SWITCH' AND code='GLOBAL_TRADE_YN')""")
        intent_count, request_count, mapping_count, reservation_count, global_trade = cursor.fetchone()
    print(json.dumps({"invalid_legacy_trade_status": invalid_status,
                      "planned_or_pending_open_live_trade": premature_open,
                      "intent_count": intent_count, "request_count": request_count,
                      "broker_mapping_count": mapping_count, "reservation_count": reservation_count,
                      "global_trade_yn": global_trade}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
