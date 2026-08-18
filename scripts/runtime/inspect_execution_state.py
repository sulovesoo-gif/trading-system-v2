"""Read-only operational execution safety and instance inspection."""

from __future__ import annotations

import json

from src.repository.database import DatabaseSettings


def main() -> int:
    import psycopg

    settings = DatabaseSettings.from_environment()
    with psycopg.connect(**settings.connection_kwargs()) as connection, connection.cursor() as cursor:
        cursor.execute("""SELECT l.live_strategy_id,l.strategy_id,m.strategy_code,m.execution_stock_code,l.live_yn
                          FROM research_live_strategy l JOIN research_strategy_master m USING(strategy_id)
                          WHERE l.live_name IN ('LIVE_HYNIX_S3_3BAR','LIVE_HYNIX_S3_5BAR',
                                                'LIVE_SAMSUNG_S1_LONG','LIVE_SAMSUNG_S2_SHORT')
                          ORDER BY l.live_strategy_id""")
        registry = cursor.fetchall()
        cursor.execute("""SELECT (SELECT count(*) FROM live_smoke_approval),
                                 (SELECT count(*) FROM live_broker_order),
                                 (SELECT count(*) FROM live_broker_fill),
                                 (SELECT count(*) FROM execution_fill_allocation),
                                 (SELECT count(*) FROM execution_logical_position),
                                 (SELECT attr1 FROM common_code WHERE group_cd='SYSTEM_SWITCH' AND code='GLOBAL_TRADE_YN')""")
        safety = cursor.fetchone()
        cursor.execute("""SELECT table_name FROM information_schema.tables WHERE table_schema='public'
                          AND table_name IN ('execution_logical_position','execution_fill_allocation',
                            'execution_reconciliation_audit','forward_execution_path','forward_candidate',
                            'forward_performance_snapshot') ORDER BY table_name""")
        tables = [row[0] for row in cursor.fetchall()]
    print(json.dumps({"registry": registry, "safety": safety, "tables": tables}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
