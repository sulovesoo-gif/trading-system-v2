"""Read-only Daily MA operations dashboard queries.

This module is intentionally a consumer of the durable Daily MA runtime state.
It never creates signals, intents, orders, fills, or collector rows.  The two
intraday calculations are explicitly labelled telemetry: they reuse the frozen
MA calculator against persisted KRX bars and are not fed back into the runtime.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from src.daily_ma_v03.evaluator import DailyMaStrategy, evaluate_ma, evaluate_strategy

KST = ZoneInfo("Asia/Seoul")


def _dicts(cursor, names: tuple[str, ...]) -> list[dict[str, Any]]:
    return [dict(zip(names, row)) for row in cursor.fetchall()]


GRID_COLUMNS = (
    "strategy_id", "strategy_name", "signal_code", "execution_code", "direction",
    "entry_fast_ma", "entry_slow_ma", "exit_fast_ma", "exit_slow_ma", "trend_ma",
    "decision_status", "selection_tier", "operation_status", "allocated_amount",
    "actual_completed_trade_count", "actual_compound_return_pct", "actual_win_rate",
    "aug_completed_trade_count", "aug_compound_return_pct", "aug_win_rate",
    "latest_closed_date", "trailing_30d_closed_count", "trailing_7d_closed_count",
    "today_closed_count", "strategy_compound_capital", "capital_epoch_no",
    "live_risk_status", "consecutive_loss_count", "open_live_trade_count",
    "open_paper_trade_count", "today_order_count", "today_fill_count", "cash_skip_today",
    "unknown_count", "reconciliation_blocked", "today_signal_status",
)


def _universe_clause(universe: str) -> tuple[str, tuple[Any, ...]]:
    value = universe.upper()
    if value == "ALL":
        return "", ()
    if value == "SELECTED":
        return "WHERE q.decision_status='SELECTED'", ()
    if value == "LIVE":
        return "WHERE q.operation_status='LIVE'", ()
    raise ValueError("universe must be ALL, SELECTED, or LIVE")


def grid_rows(pool, *, universe: str = "ALL") -> list[dict[str, Any]]:
    """One row per canonical strategy; aggregate CTEs avoid write-table locks."""
    where, values = _universe_clause(universe)
    sql = f"""
    WITH capital AS (
      SELECT strategy_id, capital_epoch_no, strategy_compound_capital
        FROM daily_strategy_compound_capital
    ), live_open AS (
      SELECT strategy_id, count(*)::int AS n
        FROM daily_strategy_live_trade WHERE trade_status='OPEN' GROUP BY strategy_id
    ), paper_open AS (
      SELECT strategy_id, count(*)::int AS n
        FROM daily_strategy_paper_trade WHERE trade_status='OPEN' GROUP BY strategy_id
    ), orders_today AS (
      SELECT i.strategy_id, count(*)::int AS orders,
             count(f.fill_id)::int AS fills
        FROM daily_strategy_live_order_intent i
        LEFT JOIN daily_strategy_live_order_request r USING(intent_id)
        LEFT JOIN live_broker_order b USING(order_request_id)
        LEFT JOIN live_broker_fill f ON f.broker_order_id=b.broker_order_id
       WHERE i.created_at::date=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')::date
       GROUP BY i.strategy_id
    ), skips AS (
      SELECT strategy_id, count(*)::int AS n
        FROM daily_strategy_live_entry_skip
       WHERE skipped_at::date=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')::date
       GROUP BY strategy_id
    ), unknowns AS (
      SELECT strategy_id, count(*)::int AS n
        FROM daily_strategy_live_order_intent
       WHERE lifecycle_status='UNKNOWN_BROKER_STATE' GROUP BY strategy_id
    ), latest_reconciliation AS (
      SELECT DISTINCT ON (stock_code) stock_code, status
        FROM execution_reconciliation_audit
       ORDER BY stock_code, checked_at DESC, reconciliation_id DESC
    ), daily_events AS (
      SELECT strategy_id,
             bool_or(event_kind='ENTRY' AND outcome='CREATED') AS entry_signal,
             bool_or(event_kind='NORMAL_EXIT' AND outcome='CREATED') AS exit_signal
        FROM daily_strategy_paper_event
       WHERE source_bar_time::date=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')::date
       GROUP BY strategy_id
    ), q AS (
      SELECT d.*, c.strategy_compound_capital, c.capital_epoch_no,
             COALESCE(r.live_risk_status, 'ENABLED') AS live_risk_status,
             COALESCE(r.consecutive_loss_streak, 0) AS consecutive_loss_count,
             COALESCE(lo.n,0) AS open_live_trade_count, COALESCE(po.n,0) AS open_paper_trade_count,
             COALESCE(ot.orders,0) AS today_order_count, COALESCE(ot.fills,0) AS today_fill_count,
             COALESCE(sk.n,0) AS cash_skip_today, COALESCE(u.n,0) AS unknown_count,
             COALESCE(lr.status <> 'PASS', FALSE) AS reconciliation_blocked,
             CASE
               WHEN COALESCE(u.n,0)>0 THEN 'UNKNOWN'
               WHEN COALESCE(lo.n,0)>0 THEN 'OPEN'
               WHEN COALESCE(ot.orders,0)>0 THEN 'ORDER_PENDING'
               WHEN de.exit_signal THEN 'EXIT_SIGNAL'
               WHEN de.entry_signal THEN 'ENTRY_SIGNAL'
               ELSE 'NO_SIGNAL'
             END AS today_signal_status
        FROM vw_daily_strategy_selection_dashboard d
        LEFT JOIN capital c USING(strategy_id)
        LEFT JOIN daily_strategy_live_risk_state r USING(strategy_id)
        LEFT JOIN live_open lo USING(strategy_id)
        LEFT JOIN paper_open po USING(strategy_id)
        LEFT JOIN orders_today ot USING(strategy_id)
        LEFT JOIN skips sk USING(strategy_id)
        LEFT JOIN unknowns u USING(strategy_id)
        LEFT JOIN latest_reconciliation lr ON lr.stock_code=d.execution_code
        LEFT JOIN daily_events de USING(strategy_id)
    ) SELECT {', '.join('q.' + x for x in GRID_COLUMNS)} FROM q {where}
       ORDER BY q.actual_compound_return_pct DESC NULLS LAST, q.strategy_id
    """
    with pool.connection() as connection, connection.cursor() as cursor:
        cursor.execute(sql, values)
        return _dicts(cursor, GRID_COLUMNS)


def _latest_krx_minute(cursor, stock_code: str) -> tuple[datetime, float] | None:
    cursor.execute("""SELECT bar_time, close_price FROM raw_stock_minute
                      WHERE stock_code=%s AND data_source='KIS' AND trading_venue='KRX'
                        AND collect_cycle='1MIN' AND bar_time::date=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')::date
                        AND bar_time::time BETWEEN TIME '09:00' AND TIME '15:30'
                        AND close_price > 0
                      ORDER BY bar_time DESC LIMIT 1""", (stock_code,))
    row = cursor.fetchone()
    return (row[0], float(row[1])) if row else None


def _prior_closes(cursor, stock_code: str, limit: int) -> list[float]:
    cursor.execute("""SELECT close_price FROM raw_stock_daily
                      WHERE stock_code=%s AND data_source='KIS' AND trading_venue='KRX'
                        AND trade_date < (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')::date
                        AND close_price > 0 ORDER BY trade_date DESC LIMIT %s""", (stock_code, limit))
    return [float(row[0]) for row in reversed(cursor.fetchall())]


def daily_proximity(pool, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Provisional 15:18-compatible values, clearly separate from final events."""
    by_signal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_signal[str(row["signal_code"])].append(row)
    result: dict[str, dict[str, Any]] = {}
    with pool.connection() as connection, connection.cursor() as cursor:
        for signal_code, strategies in by_signal.items():
            latest = _latest_krx_minute(cursor, signal_code)
            if latest is None:
                continue
            calculated_at, price = latest
            prior = _prior_closes(cursor, signal_code, 50)
            if len(prior) < 50:
                continue
            ma = evaluate_ma(prior_closes=prior, today_1518_close=price)
            for row in strategies:
                strategy = DailyMaStrategy(
                    strategy_id=str(row["strategy_id"]), signal_code=signal_code,
                    execution_code=str(row["execution_code"]), direction=str(row["direction"]),
                    entry_fast_ma=int(row["entry_fast_ma"]), entry_slow_ma=int(row["entry_slow_ma"]),
                    exit_fast_ma=int(row["exit_fast_ma"]), exit_slow_ma=int(row["exit_slow_ma"]),
                    trend_ma=int(row["trend_ma"]) if row["trend_ma"] is not None else None,
                    day20_enabled=False,
                )
                decision = evaluate_strategy(strategy=strategy, ma=ma)
                entry_gap = (ma.values_now[strategy.entry_fast_ma] - ma.values_now[strategy.entry_slow_ma]) / ma.values_now[strategy.entry_slow_ma] * 100
                exit_gap = (ma.values_now[strategy.exit_fast_ma] - ma.values_now[strategy.exit_slow_ma]) / ma.values_now[strategy.exit_slow_ma] * 100
                result[strategy.strategy_id] = {
                    "entry_gap_pct": entry_gap, "exit_gap_pct": exit_gap,
                    "trend_filter_pass": decision.trend_passed,
                    "provisional_direction": strategy.direction, "calculated_at": calculated_at,
                    "is_final_1518": calculated_at.time() == time(15, 18),
                }
    return result


def minute_telemetry(pool, rows: list[dict[str, Any]], *, recent_minutes: int = 30) -> dict[str, dict[str, Any]]:
    """Observational 1MIN cross telemetry, never used by Daily MA runtime."""
    by_signal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_signal[str(row["signal_code"])].append(row)
    result: dict[str, dict[str, Any]] = {}
    with pool.connection() as connection, connection.cursor() as cursor:
        for signal_code, strategies in by_signal.items():
            cursor.execute("""SELECT bar_time, close_price FROM raw_stock_minute
                              WHERE stock_code=%s AND data_source='KIS' AND trading_venue='KRX'
                                AND collect_cycle='1MIN' AND bar_time::date=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul')::date
                                AND bar_time::time BETWEEN TIME '09:00' AND TIME '15:30' AND close_price>0
                              ORDER BY bar_time""", (signal_code,))
            values = [(row[0], float(row[1])) for row in cursor.fetchall()]
            if len(values) < 51:
                continue
            for strategy_row in strategies:
                max_period = max(int(strategy_row["entry_fast_ma"]), int(strategy_row["entry_slow_ma"]), int(strategy_row["exit_fast_ma"]), int(strategy_row["exit_slow_ma"]), int(strategy_row["trend_ma"] or 1))
                entry_count = exit_count = 0
                latest_event_time = latest_event_type = None
                latest_gap = {"entry": None, "exit": None}
                for index in range(max_period, len(values)):
                    prior = [price for _at, price in values[:index]]
                    now = values[index][1]
                    try:
                        ma = evaluate_ma(prior_closes=prior, today_1518_close=now, periods=(int(strategy_row["entry_fast_ma"]), int(strategy_row["entry_slow_ma"]), int(strategy_row["exit_fast_ma"]), int(strategy_row["exit_slow_ma"]), int(strategy_row["trend_ma"] or 1)))
                    except ValueError:
                        continue
                    strategy = DailyMaStrategy(str(strategy_row["strategy_id"]), signal_code, str(strategy_row["execution_code"]), str(strategy_row["direction"]), int(strategy_row["entry_fast_ma"]), int(strategy_row["entry_slow_ma"]), int(strategy_row["exit_fast_ma"]), int(strategy_row["exit_slow_ma"]), int(strategy_row["trend_ma"]) if strategy_row["trend_ma"] else None, False)
                    decision = evaluate_strategy(strategy=strategy, ma=ma)
                    latest_gap = {
                        "entry": (ma.values_now[strategy.entry_fast_ma]-ma.values_now[strategy.entry_slow_ma]) / ma.values_now[strategy.entry_slow_ma]*100,
                        "exit": (ma.values_now[strategy.exit_fast_ma]-ma.values_now[strategy.exit_slow_ma]) / ma.values_now[strategy.exit_slow_ma]*100,
                    }
                    if decision.entry:
                        entry_count += 1; latest_event_time = values[index][0]; latest_event_type = "ENTRY"
                    if decision.normal_exit:
                        exit_count += 1; latest_event_time = values[index][0]; latest_event_type = "EXIT"
                last_at = values[-1][0]
                recent = latest_event_time is not None and (last_at-latest_event_time).total_seconds() <= recent_minutes * 60
                result[strategy.strategy_id] = {
                    "minute_entry_signal_count": entry_count, "minute_exit_signal_count": exit_count,
                    "latest_minute_signal_time": latest_event_time, "latest_minute_signal_type": latest_event_type,
                    "latest_minute_direction": strategy.direction, "minute_entry_gap_pct": latest_gap["entry"],
                    "minute_exit_gap_pct": latest_gap["exit"], "signal_within_recent_window": recent,
                }
    return result


def dashboard_payload(pool, *, universe: str = "ALL") -> dict[str, Any]:
    rows = grid_rows(pool, universe=universe)
    proximity = daily_proximity(pool, rows)
    minute = minute_telemetry(pool, rows)
    for row in rows:
        row["daily_proximity"] = proximity.get(row["strategy_id"])
        row["minute_telemetry"] = minute.get(row["strategy_id"])
        # Runtime events are authoritative after 15:18.  Before then only the
        # separate proximity object may influence the visual NEAR indication.
        if row["today_signal_status"] == "NO_SIGNAL" and row["daily_proximity"]:
            gap = min(abs(row["daily_proximity"]["entry_gap_pct"]), abs(row["daily_proximity"]["exit_gap_pct"]))
            if gap <= 0.15:
                row["today_signal_status"] = "ENTRY_NEAR" if abs(row["daily_proximity"]["entry_gap_pct"]) <= abs(row["daily_proximity"]["exit_gap_pct"]) else "EXIT_NEAR"
    summary = {
        "paper_tracking_strategies": 2400,
        "canonical_rows": len(grid_rows(pool, universe="ALL")),
        "selected_strategies": len(grid_rows(pool, universe="SELECTED")),
        "live_strategies": len(grid_rows(pool, universe="LIVE")),
        "three_strike_suspended": sum(row["live_risk_status"] == "THREE_STRIKE_SUSPENDED" for row in rows),
        "daily_entry_signals": sum(row["today_signal_status"] == "ENTRY_SIGNAL" for row in rows),
        "daily_exit_signals": sum(row["today_signal_status"] == "EXIT_SIGNAL" for row in rows),
        "minute_entry_signals": sum((row["minute_telemetry"] or {}).get("minute_entry_signal_count", 0) for row in rows),
        "minute_exit_signals": sum((row["minute_telemetry"] or {}).get("minute_exit_signal_count", 0) for row in rows),
        "actual_orders_today": sum(row["today_order_count"] for row in rows),
        "fills_today": sum(row["today_fill_count"] for row in rows),
        "cash_skips_today": sum(row["cash_skip_today"] for row in rows),
        "unknown_count": sum(row["unknown_count"] for row in rows),
        "reconciliation_blocked_count": sum(bool(row["reconciliation_blocked"]) for row in rows),
        "closed_today": sum(row["today_closed_count"] for row in rows),
    }
    return {"status": "OK", "generated_at": datetime.now(KST), "universe": universe.upper(), "summary": summary, "rows": rows}


def strategy_detail(pool, strategy_id: str) -> dict[str, Any]:
    """Read-only drill-down; empty LIVE ledger is a valid production result."""
    with pool.connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM vw_daily_strategy_selection_dashboard WHERE strategy_id=%s", (strategy_id,))
        base = cursor.fetchone()
        if base is None:
            raise ValueError("unknown canonical strategy")
        cursor.execute("""SELECT paper_trade_id,trade_no,entry_signal_date,paper_entry_time,paper_entry_price,
                              normal_exit_date,normal_exit_time,normal_exit_price,actual_exit_date,paper_exit_time,paper_exit_price,
                              return_pct,day20_applied,normal_return_pct,day20_delta_return_pct,trade_status
                         FROM daily_strategy_paper_trade
                        WHERE strategy_id=%s AND entry_signal_date>=DATE '2026-05-27' AND data_segment='POST_LISTING_ACTUAL'
                        ORDER BY entry_signal_date DESC,paper_trade_id DESC""", (strategy_id,))
        paper = _dicts(cursor, ("paper_trade_id","trade_no","entry_signal_date","paper_entry_time","paper_entry_price","normal_exit_date","normal_exit_time","normal_exit_price","actual_exit_date","paper_exit_time","paper_exit_price","return_pct","day20_applied","normal_return_pct","day20_delta_return_pct","trade_status"))
        cursor.execute("""SELECT live_trade_id,capital_epoch_no,trade_status,lifecycle_status,live_entry_time,live_entry_avg_price,
                              entry_quantity,live_exit_time,live_exit_avg_price,exit_quantity,buy_fee,sell_fee,sell_tax,
                              realized_pnl,return_pct,capital_settled_at
                         FROM daily_strategy_live_trade WHERE strategy_id=%s ORDER BY live_trade_id DESC""", (strategy_id,))
        live = _dicts(cursor, ("live_trade_id","capital_epoch_no","trade_status","lifecycle_status","live_entry_time","live_entry_avg_price","entry_quantity","live_exit_time","live_exit_avg_price","exit_quantity","buy_fee","sell_fee","sell_tax","realized_pnl","return_pct","capital_settled_at"))
        cursor.execute("""SELECT paper_trade_id,entry_signal_date,return_pct,data_segment
                         FROM daily_strategy_paper_trade WHERE strategy_id=%s AND entry_signal_date<DATE '2026-05-27'
                          AND trade_status='CLOSED' AND return_pct IS NOT NULL ORDER BY entry_signal_date DESC""", (strategy_id,))
        historical = _dicts(cursor, ("paper_trade_id","entry_signal_date","return_pct","provenance"))
        cursor.execute("""SELECT source_bar_time AS timestamp, 'DAILY'::text AS timeframe,
                              event_kind AS signal_type, source_snapshot->>'direction' AS direction,
                              source_snapshot->>'entry_gap_pct' AS entry_gap_pct,
                              source_snapshot->>'exit_gap_pct' AS exit_gap_pct,
                              source_snapshot->>'trend_passed' AS trend_pass,
                              CASE WHEN source_bar_time::time=TIME '15:18' THEN 'FINAL' ELSE 'PROVISIONAL' END AS signal_phase,
                              outcome
                         FROM daily_strategy_paper_event
                        WHERE strategy_id=%s ORDER BY source_bar_time DESC,paper_event_id DESC LIMIT 100""", (strategy_id,))
        signal_log = _dicts(cursor, ("timestamp","timeframe","signal_type","direction","entry_gap_pct","exit_gap_pct","trend_pass","signal_phase","outcome"))
    return {"strategy_id": strategy_id, "paper_actual": paper, "live": live,
            "historical": historical, "signal_log": signal_log}
