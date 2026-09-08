"""Non-submittable candidate signal ledger. Never imported by broker execution."""


def record_preparations(pool):
    with pool.connection() as conn, conn.transaction():
        if conn.execute("SELECT to_regclass('public.flow_v3_live_preparation_intent')").fetchone()[0] is None:
            return 0
        result=conn.execute("""INSERT INTO flow_v3_live_preparation_intent
            (strategy_id,event_id,paper_trade_id,entry_event_key,status,execution_code,
             reference_price,reference_price_time,proposed_quantity,reason)
            SELECT p.strategy_id,e.event_id,e.paper_trade_id,e.entry_event_key,
             CASE WHEN (e.execution_code,e.stock_code,e.direction) IS DISTINCT FROM
                            (p.execution_code,p.stock_code,p.direction) THEN 'BLOCKED_MAPPING'
                  WHEN p.current_capital<=0 THEN 'BLOCKED_CAPITAL'
                  WHEN p.reference_price<=0 THEN 'BLOCKED_REFERENCE'
                  ELSE 'BLOCKED_NO_SEND' END,
             e.execution_code,p.reference_price,p.reference_price_time,
             CASE WHEN e.execution_code=p.execution_code AND p.reference_price>0
                  THEN greatest(0,floor(p.current_capital/p.reference_price))::bigint END,
             'NO_BROKER_ADAPTER: indicative prior-close quantity, not submittable'
            FROM flow_v3_runtime_entry_event e JOIN flow_v3_live_preparation p USING(strategy_id)
            WHERE e.created_at >= (p.created_at AT TIME ZONE 'Asia/Seoul')
              AND e.entry_signal_time >= (p.created_at AT TIME ZONE 'Asia/Seoul')
            ON CONFLICT(strategy_id,entry_event_key) DO NOTHING""")
        conn.execute("""UPDATE flow_v3_live_preparation_intent i SET paper_trade_id=e.paper_trade_id
             FROM flow_v3_runtime_entry_event e WHERE i.event_id=e.event_id
             AND i.strategy_id=e.strategy_id AND i.paper_trade_id IS NULL
             AND e.paper_trade_id IS NOT NULL""")
        return result.rowcount
