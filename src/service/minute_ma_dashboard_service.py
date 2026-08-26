"""Read-only minute-MA dashboard queries; trading transactions are untouched."""
from __future__ import annotations


def dashboard_payload(pool,*,axis:str|None=None,operation:str|None=None) -> dict:
    conditions=[];params=[]
    if axis:
        conditions.append("data_axis=%s");params.append(axis)
    if operation:
        conditions.append("operation_status=%s");params.append(operation)
    where=" WHERE "+" AND ".join(conditions) if conditions else ""
    with pool.connection() as connection,connection.cursor() as cursor:
        cursor.execute("""SELECT data_axis,count(*)::int,
          count(*) FILTER(WHERE selection_status='SELECTED')::int,
          count(*) FILTER(WHERE selection_status='PENDING')::int,
          count(*) FILTER(WHERE operation_status='LIVE')::int,
          COALESCE(sum(paper_trade_count),0)::int,COALESCE(sum(live_trade_count),0)::int
          FROM vw_minute_ma_dashboard GROUP BY data_axis ORDER BY data_axis""")
        summary=[dict(zip(("data_axis","path_count","selected_count","pending_count","live_count",
                          "paper_trade_count","live_trade_count"),row)) for row in cursor.fetchall()]
        cursor.execute("""SELECT minute_path_id,path_key,data_axis,signal_code,execution_code,direction,
          entry_fast_ma,entry_slow_ma,exit_fast_ma,exit_slow_ma,trend_ma,selection_status,
          robustness_yn,operation_status,allocated_amount,capital_epoch_no,strategy_compound_capital,
          paper_trade_count,win_rate_pct,avg_net_return_pct,median_net_return_pct,
          compound_return_pct,paper_compound_capital,worst_trade_pct,max_concurrent_open,
          avg_hold_minutes,mdd_pct,live_trade_count,live_net_realized_pnl
          FROM vw_minute_ma_dashboard"""+where+
          " ORDER BY compound_return_pct DESC NULLS LAST,minute_path_id",tuple(params))
        columns=[d.name for d in cursor.description]
        rows=[dict(zip(columns,row)) for row in cursor.fetchall()]
    return {"status":"OK","summary":summary,"rows":rows,"row_count":len(rows),
            "send_profile":"MINUTE_MA_LIVE_SEND","actual_send_enabled":False}


def path_detail(pool,minute_path_id:int) -> dict:
    with pool.connection() as connection,connection.cursor() as cursor:
        cursor.execute("SELECT * FROM vw_minute_ma_dashboard WHERE minute_path_id=%s",(minute_path_id,))
        row=cursor.fetchone()
        if row is None:
            raise ValueError("minute MA path not found")
        path=dict(zip([d.name for d in cursor.description],row))
        cursor.execute("""SELECT minute_paper_trade_id,trade_status,entry_signal_time,entry_execution_time,
          entry_price,exit_signal_time,exit_execution_time,exit_price,exit_reason,gross_return_pct,
          net_return_pct,basis_capital,realized_pnl FROM minute_ma_paper_trade
          WHERE minute_path_id=%s ORDER BY entry_execution_time DESC,minute_paper_trade_id DESC LIMIT 500""",
          (minute_path_id,))
        paper=[dict(zip([d.name for d in cursor.description],r)) for r in cursor.fetchall()]
        cursor.execute("""SELECT minute_live_trade_id,trade_status,ownership_id,capital_epoch_no,
          capital_at_signal,entry_filled_amount,exit_filled_amount,gross_realized_pnl,
          net_realized_pnl,capital_applied_yn,created_at,updated_at
          FROM minute_ma_live_trade WHERE minute_path_id=%s ORDER BY created_at DESC LIMIT 500""",
          (minute_path_id,))
        live=[dict(zip([d.name for d in cursor.description],r)) for r in cursor.fetchall()]
    return {"status":"OK","path":path,"paper_trades":paper,"live_trades":live}
