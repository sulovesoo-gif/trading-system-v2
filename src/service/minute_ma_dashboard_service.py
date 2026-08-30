"""Read-only, paginated Minute-MA operating dashboard."""
from __future__ import annotations

SCOPES = {"V1_LIVE", "V1_ALL", "LEGACY"}
PAGE_SIZES = {20, 50}
V1_SORTS = {
    "rank": "current_rank ASC NULLS LAST,minute_policy_path_id",
    "strategy": "source_daily_strategy_id,minute_policy_path_id",
    "capital": "v1_strategy_compound_capital DESC NULLS LAST,minute_policy_path_id",
    "open": "total_open_count DESC,minute_policy_path_id",
}
LEGACY_SORTS = {
    "performance": "historical_compound_return_pct DESC NULLS LAST,d.minute_path_id",
    "strategy": "d.source_daily_strategy_id,d.minute_path_id",
    "axis": "d.data_axis,d.minute_path_id",
}

def _dicts(cursor):
    cols = [d.name for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]

def _page(value, default=1):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default

def _page_size(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 20
    return parsed if parsed in PAGE_SIZES else 20

def _operational(cursor):
    cursor.execute("""SELECT
      (SELECT count(*) FROM minute_ma_path WHERE is_enabled='Y')::int total_paths,
      (SELECT count(*) FROM minute_ma_operation WHERE effective_to IS NULL AND operation_status='PAPER')::int paper_paths,
      (SELECT count(*) FROM minute_ma_operation WHERE effective_to IS NULL AND operation_status='LIVE')::int live_paths,
      (SELECT count(*) FROM minute_ma_paper_trade WHERE trade_status='OPEN')::int open_paper,
      (SELECT count(*) FROM minute_ma_live_trade WHERE trade_status='OPEN')::int open_live,
      (SELECT count(*) FROM minute_ma_paper_event WHERE event_type='ENTRY' AND source_bar_time::date=CURRENT_DATE)::int today_paper_entry,
      (SELECT count(*) FROM minute_ma_paper_event WHERE event_type IN('EXIT','EOD_EXIT') AND source_bar_time::date=CURRENT_DATE)::int today_paper_exit,
      (SELECT count(*) FROM minute_ma_live_signal_event WHERE event_type='ENTRY' AND source_bar_time::date=CURRENT_DATE)::int today_live_entry,
      (SELECT count(*) FROM minute_ma_live_signal_event WHERE event_type='EXIT' AND source_bar_time::date=CURRENT_DATE)::int today_live_exit,
      (SELECT count(*) FROM live_broker_order b JOIN minute_ma_live_order_link l USING(broker_order_id) WHERE b.created_at::date=CURRENT_DATE)::int today_orders,
      (SELECT COALESCE(sum(a.delta_quantity),0) FROM minute_ma_live_checkpoint_allocation a WHERE a.created_at::date=CURRENT_DATE)::int today_filled_qty,
      (SELECT count(*) FROM minute_ma_live_entry_skip WHERE created_at::date=CURRENT_DATE)::int today_skips,
      (SELECT send_enabled FROM minute_ma_send_profile WHERE profile_code='MINUTE_MA_LIVE_SEND') send_enabled,
      (SELECT max(updated_at) FROM minute_ma_runtime_cursor)::timestamp last_runtime_at""")
    return dict(zip([d.name for d in cursor.description], cursor.fetchone()))

def _v1_summary(cursor):
    cursor.execute("""SELECT count(*)::int policy_paths,
      count(*) FILTER(WHERE proposed_initial_capital IS NOT NULL)::int candidates,
      COALESCE(sum(proposed_initial_capital),0) proposed_capital,
      count(*) FILTER(WHERE v1_selection_status='SELECTED')::int selected_paths,
      count(*) FILTER(WHERE v1_operation_status='LIVE')::int live_paths,
      count(*) FILTER(WHERE v1_strategy_compound_capital IS NOT NULL)::int capital_epochs,
      COALESCE(sum(v1_strategy_compound_capital),0) strategy_compound_capital,
      COALESCE(sum(total_open_count),0)::int open_trades,
      COALESCE(sum(overnight_open_count),0)::int overnight_open,
      COALESCE(sum(stop_exit_count),0)::int stop_exits
      FROM vw_minute_ma_v1_policy_dashboard""")
    return dict(zip([d.name for d in cursor.description], cursor.fetchone()))

def _v1_page(cursor, *, scope, page, page_size, sort, search, direction):
    conditions, params = [], []
    if scope == "V1_LIVE":
        conditions.append("v1_operation_status='LIVE'")
    if direction in {"LONG", "SHORT"}:
        conditions.append("direction=%s"); params.append(direction)
    if search:
        conditions.append("(source_daily_strategy_id ILIKE %s OR signal_code ILIKE %s OR execution_code ILIKE %s)")
        term = f"%{search[:80]}%"; params.extend((term, term, term))
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    cursor.execute("SELECT count(*)::int FROM vw_minute_ma_v1_policy_dashboard" + where, tuple(params))
    total = cursor.fetchone()[0]
    order = V1_SORTS.get(sort, V1_SORTS["rank"])
    cursor.execute("SELECT * FROM vw_minute_ma_v1_policy_dashboard" + where +
                   f" ORDER BY {order} LIMIT %s OFFSET %s",
                   tuple(params + [page_size, (page - 1) * page_size]))
    rows = _dicts(cursor)
    for row in rows:
        row["top20_consecutive_days"] = row.get("current_top20_consecutive_days", row.get("top20_consecutive_days", 0))
    return rows, total

def _legacy_page(cursor, *, axis, operation, page, page_size, sort, search, direction):
    conditions, params = [], []
    if axis:
        conditions.append("d.data_axis=%s"); params.append(axis)
    if operation in {"LIVE", "PAPER"}:
        conditions.append("d.operation_status=%s"); params.append(operation)
    if direction in {"LONG", "SHORT"}:
        conditions.append("d.direction=%s"); params.append(direction)
    if search:
        conditions.append("(d.source_daily_strategy_id ILIKE %s OR d.signal_code ILIKE %s OR d.execution_code ILIKE %s)")
        term = f"%{search[:80]}%"; params.extend((term, term, term))
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    cursor.execute("SELECT count(*)::int FROM vw_minute_ma_dashboard d" + where, tuple(params))
    total = cursor.fetchone()[0]
    order = LEGACY_SORTS.get(sort, LEGACY_SORTS["performance"])
    cursor.execute("""WITH paper AS (SELECT minute_path_id,
         count(*) FILTER(WHERE trade_status='OPEN')::int open_paper,
         max(entry_signal_time) latest_entry,max(exit_signal_time) latest_exit
         FROM minute_ma_paper_trade GROUP BY minute_path_id),
      live AS (SELECT minute_path_id,count(*) FILTER(WHERE trade_status='OPEN')::int open_live
         FROM minute_ma_live_trade GROUP BY minute_path_id),
      curs AS (SELECT data_axis,signal_code,max(last_source_bar_time) last_evaluated_at
         FROM minute_ma_runtime_cursor GROUP BY data_axis,signal_code)
      SELECT d.*,sel.compound_return_pct historical_compound_return_pct,
         sel.completed_trade_count historical_trade_count,sel.win_rate_pct historical_win_rate_pct,
         COALESCE(p.open_paper,0) open_paper_count,COALESCE(l.open_live,0) open_live_count,
         p.latest_entry,p.latest_exit,curs.last_evaluated_at
      FROM vw_minute_ma_dashboard d LEFT JOIN vw_minute_ma_current_selection sel USING(minute_path_id)
      LEFT JOIN paper p USING(minute_path_id) LEFT JOIN live l USING(minute_path_id)
      LEFT JOIN curs ON curs.data_axis=d.data_axis AND curs.signal_code=d.signal_code""" + where +
      f" ORDER BY {order} LIMIT %s OFFSET %s", tuple(params + [page_size, (page - 1) * page_size]))
    return _dicts(cursor), total

def dashboard_payload(pool, *, scope="V1_LIVE", axis=None, operation=None, page=1,
                      page_size=20, sort=None, search=None, direction=None) -> dict:
    """Return one requested page; default entry is only the 20 V1 LIVE paths."""
    scope = scope if scope in SCOPES else "V1_LIVE"
    page, page_size = _page(page), _page_size(page_size)
    search = (search or "").strip()
    with pool.connection() as c, c.cursor() as q:
        operational = _operational(q)
        q.execute("SELECT to_regclass('public.vw_minute_ma_v1_policy_dashboard')")
        has_v1 = q.fetchone()[0] is not None
        v1_summary = _v1_summary(q) if has_v1 else {
            "policy_paths": 0, "candidates": 0, "proposed_capital": 0, "selected_paths": 0,
            "live_paths": 0, "capital_epochs": 0, "strategy_compound_capital": 0,
            "open_trades": 0, "overnight_open": 0, "stop_exits": 0}
        if scope.startswith("V1"):
            rows, total = _v1_page(q, scope=scope, page=page, page_size=page_size,
                                   sort=sort, search=search, direction=direction) if has_v1 else ([], 0)
        else:
            rows, total = _legacy_page(q, axis=axis, operation=operation, page=page,
                                       page_size=page_size, sort=sort, search=search,
                                       direction=direction)
    return {"status": "OK", "scope": scope, "operational": operational,
            "v1_summary": v1_summary, "rows": rows, "row_count": len(rows),
            "page": page, "page_size": page_size, "total_count": total,
            "total_pages": max(1, (total + page_size - 1) // page_size),
            "send_profile": "MINUTE_MA_LIVE_SEND",
            "actual_send_enabled": operational.get("send_enabled") == "Y"}

def path_detail(pool, minute_path_id: int) -> dict:
    with pool.connection() as c, c.cursor() as q:
        q.execute("SELECT d.*,s.compound_return_pct historical_compound_return_pct,s.source_row historical_source FROM vw_minute_ma_dashboard d LEFT JOIN vw_minute_ma_current_selection s USING(minute_path_id) WHERE d.minute_path_id=%s", (minute_path_id,)); row = q.fetchone()
        if row is None: raise ValueError("minute MA path not found")
        path = dict(zip([d.name for d in q.description], row))
        q.execute("SELECT * FROM minute_ma_paper_trade WHERE minute_path_id=%s ORDER BY entry_execution_time DESC LIMIT 100", (minute_path_id,)); paper = _dicts(q)
        q.execute("SELECT * FROM minute_ma_live_trade WHERE minute_path_id=%s ORDER BY created_at DESC LIMIT 100", (minute_path_id,)); live = _dicts(q)
        q.execute("""SELECT i.*,r.order_request_id,r.status request_status,r.requested_notional,
          b.broker_order_number,b.status broker_status,cp.cumulative_filled_qty,cp.cumulative_filled_amount
          FROM minute_ma_live_intent i LEFT JOIN minute_ma_live_order_link l USING(intent_id)
          LEFT JOIN live_order_request r USING(order_request_id) LEFT JOIN live_broker_order b USING(broker_order_id)
          LEFT JOIN minute_ma_live_fill_checkpoint cp USING(broker_order_id)
          WHERE i.minute_path_id=%s ORDER BY i.created_at DESC LIMIT 100""", (minute_path_id,)); orders = _dicts(q)
        q.execute("""SELECT a.*,s.trade_date,s.execution_stock_code,s.finalization_status FROM minute_ma_live_broker_cost_allocation a
          JOIN minute_ma_live_broker_cost_snapshot s USING(broker_cost_snapshot_id)
          JOIN minute_ma_live_trade t USING(minute_live_trade_id) WHERE t.minute_path_id=%s ORDER BY s.trade_date DESC LIMIT 100""", (minute_path_id,)); costs = _dicts(q)
        q.execute("""SELECT cs.* FROM minute_ma_live_capital_settlement cs JOIN minute_ma_live_trade t USING(minute_live_trade_id)
          WHERE t.minute_path_id=%s ORDER BY cs.settled_at DESC LIMIT 100""", (minute_path_id,)); settlements = _dicts(q)
    return {"status": "OK", "path": path, "paper_trades": paper, "live_trades": live,
            "orders": orders, "broker_costs": costs, "settlements": settlements}
