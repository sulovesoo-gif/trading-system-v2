"""Read-only Daily MA operations dashboard queries.

This module is intentionally a consumer of the durable Daily MA runtime state.
It never creates signals, intents, orders, fills, or collector rows.  The two
intraday calculations are explicitly labelled telemetry: they reuse the frozen
MA calculator against persisted KRX bars and are not fed back into the runtime.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from src.daily_ma_v03.evaluator import DailyMaStrategy, evaluate_ma, evaluate_strategy
from src.ma_crossover import GapTransition, classify_gap_transition

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
    "daily_signal_confirmed_at", "today_intent_count", "today_request_count",
    "today_submit_count", "today_requested_quantity", "today_filled_quantity",
    "today_remaining_quantity", "today_order_lifecycle", "today_cash_skip_reason",
)
BASE_GRID_COLUMNS = GRID_COLUMNS[:20]


def _dashboard_date(value: date | None) -> date:
    return value or datetime.now(KST).date()


def _universe_clause(universe: str) -> tuple[str, tuple[Any, ...]]:
    value = universe.upper()
    if value == "ALL":
        return "", ()
    if value == "SELECTED":
        return "WHERE q.decision_status='SELECTED'", ()
    if value == "LIVE":
        return "WHERE q.operation_status='LIVE'", ()
    raise ValueError("universe must be ALL, SELECTED, or LIVE")


def grid_rows(pool, *, universe: str = "ALL", as_of_date: date | None = None) -> list[dict[str, Any]]:
    """One row per canonical strategy; aggregate CTEs avoid write-table locks."""
    as_of_date = _dashboard_date(as_of_date)
    where, values = _universe_clause(universe)
    sql = f"""
    WITH params AS (SELECT %s::date AS as_of_date), capital AS (
      SELECT strategy_id, capital_epoch_no, strategy_compound_capital
        FROM daily_strategy_compound_capital
    ), live_open AS (
      SELECT strategy_id, count(*)::int AS n
        FROM daily_strategy_live_trade
       WHERE live_entry_time::date <= (SELECT as_of_date FROM params)
         AND (live_exit_time IS NULL OR live_exit_time::date > (SELECT as_of_date FROM params))
       GROUP BY strategy_id
    ), paper_open AS (
      SELECT strategy_id, count(*)::int AS n
        FROM daily_strategy_paper_trade
       WHERE entry_signal_date <= (SELECT as_of_date FROM params)
         AND (actual_exit_date IS NULL OR actual_exit_date > (SELECT as_of_date FROM params))
       GROUP BY strategy_id
    ), activity AS (
      SELECT strategy_id, max(actual_exit_date) AS latest_closed_date,
             count(*) FILTER (WHERE actual_exit_date > (SELECT as_of_date FROM params)-30)::int AS trailing_30d_closed_count,
             count(*) FILTER (WHERE actual_exit_date > (SELECT as_of_date FROM params)-7)::int AS trailing_7d_closed_count,
             count(*) FILTER (WHERE actual_exit_date = (SELECT as_of_date FROM params))::int AS today_closed_count
        FROM daily_strategy_paper_trade
       WHERE actual_exit_date <= (SELECT as_of_date FROM params)
       GROUP BY strategy_id
    ), fill_by_order AS (
      SELECT broker_order_id, count(*)::int AS fills, sum(fill_quantity)::int AS filled_quantity
        FROM live_broker_fill GROUP BY broker_order_id
    ), orders_today AS (
      SELECT i.strategy_id, count(DISTINCT i.intent_id)::int AS intents,
             count(DISTINCT r.order_request_id)::int AS requests,
             count(DISTINCT b.broker_order_id)::int AS submitted,
             COALESCE(sum(f.fills),0)::int AS fills,
             COALESCE(sum(r.quantity),0)::int AS requested_quantity,
             COALESCE(sum(f.filled_quantity),0)::int AS filled_quantity,
             max(COALESCE(b.status,r.request_status,i.lifecycle_status)) AS lifecycle
        FROM daily_strategy_live_order_intent i
        LEFT JOIN daily_strategy_live_order_request r USING(intent_id)
        LEFT JOIN live_broker_order b USING(order_request_id)
        LEFT JOIN fill_by_order f ON f.broker_order_id=b.broker_order_id
       WHERE i.created_at::date=(SELECT as_of_date FROM params)
       GROUP BY i.strategy_id
    ), skips AS (
      SELECT strategy_id, count(*)::int AS n, max(skip_reason) AS reason
        FROM daily_strategy_live_entry_skip
       WHERE skipped_at::date=(SELECT as_of_date FROM params)
       GROUP BY strategy_id
    ), unknowns AS (
      SELECT strategy_id, count(*)::int AS n
        FROM daily_strategy_live_order_intent
       WHERE lifecycle_status='UNKNOWN_BROKER_STATE'
         AND created_at::date <= (SELECT as_of_date FROM params) GROUP BY strategy_id
    ), latest_reconciliation AS (
      SELECT DISTINCT ON (stock_code) stock_code, status
        FROM execution_reconciliation_audit
       WHERE checked_at::date <= (SELECT as_of_date FROM params)
       ORDER BY stock_code, checked_at DESC, reconciliation_id DESC
    ), daily_events AS (
      SELECT strategy_id,
             bool_or(event_kind='ENTRY' AND outcome='CREATED') AS entry_signal,
             bool_or(event_kind='NORMAL_EXIT' AND outcome='CREATED') AS exit_signal,
             max(source_bar_time) AS confirmed_at
        FROM daily_strategy_paper_event
       WHERE source_bar_time::date=(SELECT as_of_date FROM params)
       GROUP BY strategy_id
    ), q AS (
      SELECT {', '.join('d.' + x for x in BASE_GRID_COLUMNS)},
             a.latest_closed_date, COALESCE(a.trailing_30d_closed_count,0) AS trailing_30d_closed_count,
             COALESCE(a.trailing_7d_closed_count,0) AS trailing_7d_closed_count,
             COALESCE(a.today_closed_count,0) AS today_closed_count,
             c.strategy_compound_capital, c.capital_epoch_no,
             COALESCE(r.live_risk_status, 'ENABLED') AS live_risk_status,
             COALESCE(r.consecutive_loss_streak, 0) AS consecutive_loss_count,
             COALESCE(lo.n,0) AS open_live_trade_count, COALESCE(po.n,0) AS open_paper_trade_count,
             COALESCE(ot.submitted,0) AS today_order_count, COALESCE(ot.fills,0) AS today_fill_count,
             COALESCE(ot.intents,0) AS today_intent_count, COALESCE(ot.requests,0) AS today_request_count,
             COALESCE(ot.submitted,0) AS today_submit_count,
             COALESCE(ot.requested_quantity,0) AS today_requested_quantity,
             COALESCE(ot.filled_quantity,0) AS today_filled_quantity,
             GREATEST(COALESCE(ot.requested_quantity,0)-COALESCE(ot.filled_quantity,0),0) AS today_remaining_quantity,
             ot.lifecycle AS today_order_lifecycle, sk.reason AS today_cash_skip_reason,
             COALESCE(sk.n,0) AS cash_skip_today, COALESCE(u.n,0) AS unknown_count,
             COALESCE(lr.status <> 'PASS', FALSE) AS reconciliation_blocked,
             CASE
               WHEN COALESCE(u.n,0)>0 THEN 'UNKNOWN'
               WHEN COALESCE(ot.filled_quantity,0)>0 AND COALESCE(ot.requested_quantity,0)>COALESCE(ot.filled_quantity,0) THEN 'PARTIALLY_FILLED'
               WHEN COALESCE(lo.n,0)>0 THEN 'OPEN'
               WHEN COALESCE(ot.intents,0)>0 AND EXISTS (SELECT 1 FROM daily_strategy_live_order_intent xi WHERE xi.strategy_id=d.strategy_id AND xi.intent_type='EXIT' AND xi.created_at::date=(SELECT as_of_date FROM params)) THEN 'EXIT_PENDING'
               WHEN COALESCE(ot.intents,0)>0 THEN 'ORDER_PENDING'
               WHEN COALESCE(sk.n,0)>0 OR (d.operation_status='LIVE' AND COALESCE(r.live_risk_status,'ENABLED')<>'ENABLED') OR COALESCE(lr.status <> 'PASS', FALSE) THEN 'ENTRY_BLOCKED'
               WHEN de.exit_signal THEN 'EXIT_SIGNAL'
               WHEN de.entry_signal THEN 'ENTRY_SIGNAL'
               ELSE 'NO_SIGNAL'
             END AS today_signal_status, de.confirmed_at AS daily_signal_confirmed_at
        FROM vw_daily_strategy_selection_dashboard d
        LEFT JOIN capital c USING(strategy_id)
        LEFT JOIN daily_strategy_live_risk_state r USING(strategy_id)
        LEFT JOIN live_open lo USING(strategy_id)
        LEFT JOIN paper_open po USING(strategy_id)
        LEFT JOIN activity a USING(strategy_id)
        LEFT JOIN orders_today ot USING(strategy_id)
        LEFT JOIN skips sk USING(strategy_id)
        LEFT JOIN unknowns u USING(strategy_id)
        LEFT JOIN latest_reconciliation lr ON lr.stock_code=d.execution_code
        LEFT JOIN daily_events de USING(strategy_id)
    ) SELECT {', '.join('q.' + x for x in GRID_COLUMNS)} FROM q {where}
       ORDER BY q.actual_compound_return_pct DESC NULLS LAST, q.strategy_id
    """
    with pool.connection() as connection, connection.cursor() as cursor:
        cursor.execute(sql, (as_of_date, *values))
        rows = _dicts(cursor, GRID_COLUMNS)
        codes = sorted({str(row[code]) for row in rows for code in ("signal_code", "execution_code")})
        if codes:
            cursor.execute("""SELECT code, NULLIF(BTRIM(code_name),'')
                              FROM common_code
                             WHERE group_cd='STOCK' AND code=ANY(%s)""", (codes,))
            stock_names = {str(code): str(name) for code, name in cursor.fetchall() if name is not None}
        else:
            stock_names = {}
    for row in rows:
        row["signal_name"] = stock_names.get(str(row["signal_code"]))
        row["execution_name"] = stock_names.get(str(row["execution_code"]))
    return rows


def universe_counts(pool) -> dict[str, int]:
    """Lightweight header counts; do not rerun the full telemetry grid query."""
    with pool.connection() as connection, connection.cursor() as cursor:
        cursor.execute("""SELECT count(*)::int,
                                 count(*) FILTER (WHERE decision_status='SELECTED')::int,
                                 count(*) FILTER (WHERE operation_status='LIVE')::int
                            FROM vw_daily_strategy_selection_dashboard""")
        canonical, selected, live = cursor.fetchone()
    return {"ALL": canonical, "SELECTED": selected, "LIVE": live}


def _latest_krx_minute(cursor, stock_code: str, as_of_date: date) -> tuple[datetime, float] | None:
    cursor.execute("""SELECT bar_time, close_price FROM raw_stock_minute
                      WHERE stock_code=%s AND data_source='KIS' AND trading_venue='KRX'
                        AND collect_cycle='1MIN' AND bar_time::date=%s
                        AND bar_time::time BETWEEN TIME '09:00' AND TIME '15:30'
                        AND close_price > 0
                      ORDER BY bar_time DESC LIMIT 1""", (stock_code, as_of_date))
    row = cursor.fetchone()
    return (row[0], float(row[1])) if row else None


def _prior_closes(cursor, stock_code: str, limit: int, as_of_date: date) -> list[float]:
    cursor.execute("""SELECT close_price FROM raw_stock_daily
                      WHERE stock_code=%s AND data_source='KIS' AND trading_venue='KRX'
                        AND trade_date < %s
                        AND close_price > 0 ORDER BY trade_date DESC LIMIT %s""", (stock_code, as_of_date, limit))
    return [float(row[0]) for row in reversed(cursor.fetchall())]


def daily_proximity(pool, rows: list[dict[str, Any]], *, as_of_date: date | None = None) -> dict[str, dict[str, Any]]:
    """Provisional 15:18-compatible values, clearly separate from final events."""
    as_of_date = _dashboard_date(as_of_date)
    by_signal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_signal[str(row["signal_code"])].append(row)
    result: dict[str, dict[str, Any]] = {}
    with pool.connection() as connection, connection.cursor() as cursor:
        for signal_code, strategies in by_signal.items():
            latest = _latest_krx_minute(cursor, signal_code, as_of_date)
            if latest is None:
                continue
            calculated_at, price = latest
            prior = _prior_closes(cursor, signal_code, 50, as_of_date)
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
                previous_entry_gap = (ma.values_previous[strategy.entry_fast_ma] - ma.values_previous[strategy.entry_slow_ma]) / ma.values_previous[strategy.entry_slow_ma] * 100
                entry_gap = (ma.values_now[strategy.entry_fast_ma] - ma.values_now[strategy.entry_slow_ma]) / ma.values_now[strategy.entry_slow_ma] * 100
                previous_exit_gap = (ma.values_previous[strategy.exit_fast_ma] - ma.values_previous[strategy.exit_slow_ma]) / ma.values_previous[strategy.exit_slow_ma] * 100
                exit_gap = (ma.values_now[strategy.exit_fast_ma] - ma.values_now[strategy.exit_slow_ma]) / ma.values_now[strategy.exit_slow_ma] * 100
                provisional_status, entry_transition, exit_transition = proximity_signal_status(
                    direction=strategy.direction,
                    previous_entry_gap_pct=previous_entry_gap,
                    current_entry_gap_pct=entry_gap,
                    previous_exit_gap_pct=previous_exit_gap,
                    current_exit_gap_pct=exit_gap,
                )
                result[strategy.strategy_id] = {
                    "previous_entry_gap_pct": previous_entry_gap,
                    "entry_gap_pct": entry_gap,
                    "previous_exit_gap_pct": previous_exit_gap,
                    "exit_gap_pct": exit_gap,
                    "entry_transition": entry_transition.value,
                    "exit_transition": exit_transition.value,
                    "provisional_status": provisional_status,
                    "trend_filter_pass": decision.trend_passed,
                    "provisional_direction": strategy.direction, "calculated_at": calculated_at,
                    "is_final_1518": calculated_at.time() == time(15, 18),
                }
    return result


def proximity_signal_status(*, direction: str,
                            previous_entry_gap_pct: float, current_entry_gap_pct: float,
                            previous_exit_gap_pct: float, current_exit_gap_pct: float,
                            threshold_pct: float = 0.15,
                            ) -> tuple[str | None, GapTransition, GapTransition]:
    """Map directional gap transitions to provisional strategy observations."""
    direction = direction.upper()
    entry_transition = classify_gap_transition(
        previous_gap=previous_entry_gap_pct,
        current_gap=current_entry_gap_pct,
        near_threshold=threshold_pct,
    )
    exit_transition = classify_gap_transition(
        previous_gap=previous_exit_gap_pct,
        current_gap=current_exit_gap_pct,
        near_threshold=threshold_pct,
    )
    if direction == "LONG":
        entry_cross, entry_near = GapTransition.UP_CROSS, GapTransition.UP_NEAR
        exit_cross, exit_near = GapTransition.DOWN_CROSS, GapTransition.DOWN_NEAR
    elif direction == "SHORT":
        entry_cross, entry_near = GapTransition.DOWN_CROSS, GapTransition.DOWN_NEAR
        exit_cross, exit_near = GapTransition.UP_CROSS, GapTransition.UP_NEAR
    else:
        return None, entry_transition, exit_transition

    entry_cross_seen = entry_transition is entry_cross
    exit_cross_seen = exit_transition is exit_cross
    if entry_cross_seen and exit_cross_seen:
        return "ENTRY_EXIT_CROSS_OBSERVED", entry_transition, exit_transition
    if entry_cross_seen:
        return "ENTRY_CROSS_OBSERVED", entry_transition, exit_transition
    if exit_cross_seen:
        return "EXIT_CROSS_OBSERVED", entry_transition, exit_transition

    near_candidates: list[tuple[float, str]] = []
    if entry_transition is entry_near:
        near_candidates.append((abs(current_entry_gap_pct), "ENTRY_NEAR"))
    if exit_transition is exit_near:
        near_candidates.append((abs(current_exit_gap_pct), "EXIT_NEAR"))
    status = min(near_candidates, key=lambda item: item[0])[1] if near_candidates else None
    return status, entry_transition, exit_transition


def _minute_cross_telemetry(values: Sequence[tuple[datetime, float]], *, strategy: DailyMaStrategy) -> dict[str, Any]:
    """Return first-transition events and latest read-only 1MIN telemetry.

    This is intentionally a pure read-only telemetry helper.  A signal is
    emitted once at its completed-bar transition; maintaining the condition on
    subsequent bars never creates another event.  Re-running the same raw-bar
    sequence yields the same unique ``(strategy_id, bar_time, event_type)`` set.
    """
    periods = (strategy.entry_fast_ma, strategy.entry_slow_ma,
               strategy.exit_fast_ma, strategy.exit_slow_ma, strategy.trend_ma or 1)
    max_period = max(periods)
    events: list[tuple[datetime, str]] = []
    seen: set[tuple[str, datetime, str]] = set()
    latest_gap = {"entry": None, "exit": None}
    for index in range(max_period, len(values)):
        prior = [price for _at, price in values[:index]]
        try:
            ma = evaluate_ma(prior_closes=prior, today_1518_close=values[index][1], periods=periods)
        except ValueError:
            continue
        latest_gap = {
            "entry": (ma.values_now[strategy.entry_fast_ma] - ma.values_now[strategy.entry_slow_ma]) / ma.values_now[strategy.entry_slow_ma] * 100,
            "exit": (ma.values_now[strategy.exit_fast_ma] - ma.values_now[strategy.exit_slow_ma]) / ma.values_now[strategy.exit_slow_ma] * 100,
        }
        decision = evaluate_strategy(strategy=strategy, ma=ma)
        for event_type, occurred in (("ENTRY", decision.entry), ("EXIT", decision.normal_exit)):
            identity = (strategy.strategy_id, values[index][0], event_type)
            if occurred and identity not in seen:
                seen.add(identity)
                events.append((values[index][0], event_type))
    latest_event_time, latest_event_type = events[-1] if events else (None, None)
    return {
        "events": events,
        "minute_entry_signal_count": sum(event_type == "ENTRY" for _at, event_type in events),
        "minute_exit_signal_count": sum(event_type == "EXIT" for _at, event_type in events),
        "latest_minute_signal_time": latest_event_time,
        "latest_minute_signal_type": latest_event_type,
        "minute_entry_gap_pct": latest_gap["entry"],
        "minute_exit_gap_pct": latest_gap["exit"],
    }


def minute_cross_events(values: Sequence[tuple[datetime, float]], *, strategy: DailyMaStrategy) -> list[tuple[datetime, str]]:
    """Public crossover-event projection used by telemetry contract tests."""
    return _minute_cross_telemetry(values, strategy=strategy)["events"]


def minute_telemetry(pool, rows: list[dict[str, Any]], *, recent_minutes: int = 30,
                     as_of_date: date | None = None) -> dict[str, dict[str, Any]]:
    """Observational 1MIN cross telemetry, never used by Daily MA runtime."""
    as_of_date = _dashboard_date(as_of_date)
    by_signal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_signal[str(row["signal_code"])].append(row)
    result: dict[str, dict[str, Any]] = {}
    with pool.connection() as connection, connection.cursor() as cursor:
        for signal_code, strategies in by_signal.items():
            cursor.execute("""SELECT bar_time, close_price FROM raw_stock_minute
                              WHERE stock_code=%s AND data_source='KIS' AND trading_venue='KRX'
                                AND collect_cycle='1MIN' AND bar_time::date=%s
                                AND bar_time::time BETWEEN TIME '09:00' AND TIME '15:30' AND close_price>0
                              ORDER BY bar_time""", (signal_code, as_of_date))
            values = [(row[0], float(row[1])) for row in cursor.fetchall()]
            if len(values) < 51:
                continue
            cached: dict[tuple[Any, ...], dict[str, Any]] = {}
            for strategy_row in strategies:
                strategy = DailyMaStrategy(str(strategy_row["strategy_id"]), signal_code, str(strategy_row["execution_code"]), str(strategy_row["direction"]), int(strategy_row["entry_fast_ma"]), int(strategy_row["entry_slow_ma"]), int(strategy_row["exit_fast_ma"]), int(strategy_row["exit_slow_ma"]), int(strategy_row["trend_ma"]) if strategy_row["trend_ma"] else None, False)
                key = (strategy.direction, strategy.entry_fast_ma, strategy.entry_slow_ma,
                       strategy.exit_fast_ma, strategy.exit_slow_ma, strategy.trend_ma)
                telemetry = cached.get(key)
                if telemetry is None:
                    telemetry = _minute_cross_telemetry(values, strategy=strategy)
                    cached[key] = telemetry
                last_at = values[-1][0]
                recent = telemetry["latest_minute_signal_time"] is not None and (last_at-telemetry["latest_minute_signal_time"]).total_seconds() <= recent_minutes * 60
                result[strategy.strategy_id] = {
                    **{name: value for name, value in telemetry.items() if name != "events"},
                    "latest_minute_direction": strategy.direction,
                    "signal_within_recent_window": recent,
                }
    return result


def dashboard_payload(pool, *, universe: str = "ALL", as_of_date: date | None = None) -> dict[str, Any]:
    as_of_date = _dashboard_date(as_of_date)
    rows = grid_rows(pool, universe=universe, as_of_date=as_of_date)
    proximity = daily_proximity(pool, rows, as_of_date=as_of_date)
    minute = minute_telemetry(pool, rows, as_of_date=as_of_date)
    for row in rows:
        row["daily_proximity"] = proximity.get(row["strategy_id"])
        row["minute_telemetry"] = minute.get(row["strategy_id"])
        # Runtime events are authoritative after 15:18.  Before then only the
        # separate observation object may influence provisional visual status.
        if row["today_signal_status"] == "NO_SIGNAL" and row["daily_proximity"]:
            provisional_status = row["daily_proximity"]["provisional_status"]
            if provisional_status is not None:
                row["today_signal_status"] = provisional_status
    counts = universe_counts(pool)
    summary = {
        "paper_tracking_strategies": 2400,
        "canonical_rows": counts["ALL"],
        "selected_strategies": counts["SELECTED"],
        "live_strategies": counts["LIVE"],
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
    return {"status": "OK", "generated_at": datetime.now(KST), "as_of_date": as_of_date,
            "universe": universe.upper(), "summary": summary, "rows": rows}


def strategy_detail(pool, strategy_id: str, *, as_of_date: date | None = None) -> dict[str, Any]:
    """Read-only drill-down; empty LIVE ledger is a valid production result."""
    as_of_date = _dashboard_date(as_of_date)
    with pool.connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM vw_daily_strategy_selection_dashboard WHERE strategy_id=%s", (strategy_id,))
        base = cursor.fetchone()
        if base is None:
            raise ValueError("unknown canonical strategy")
        cursor.execute("""SELECT paper_trade_id,trade_no,entry_signal_date,paper_entry_time,paper_entry_price,
                              normal_exit_date,normal_exit_time,normal_exit_price,actual_exit_date,paper_exit_time,paper_exit_price,
                              return_pct,day20_applied,normal_return_pct,day20_delta_return_pct,trade_status
                         FROM daily_strategy_paper_trade
                        WHERE strategy_id=%s AND entry_signal_date>=DATE '2026-05-27' AND entry_signal_date<=%s
                          AND data_segment='POST_LISTING_ACTUAL'
                        ORDER BY entry_signal_date DESC,paper_trade_id DESC""", (strategy_id, as_of_date))
        paper = _dicts(cursor, ("paper_trade_id","trade_no","entry_signal_date","paper_entry_time","paper_entry_price","normal_exit_date","normal_exit_time","normal_exit_price","actual_exit_date","paper_exit_time","paper_exit_price","return_pct","day20_applied","normal_return_pct","day20_delta_return_pct","trade_status"))
        cursor.execute("""SELECT live_trade_id,capital_epoch_no,trade_status,lifecycle_status,live_entry_time,live_entry_avg_price,
                              entry_quantity,live_exit_time,live_exit_avg_price,exit_quantity,buy_fee,sell_fee,sell_tax,
                              realized_pnl,return_pct,capital_settled_at
                         FROM daily_strategy_live_trade WHERE strategy_id=%s ORDER BY live_trade_id DESC""", (strategy_id,))
        live = _dicts(cursor, ("live_trade_id","capital_epoch_no","trade_status","lifecycle_status","live_entry_time","live_entry_avg_price","entry_quantity","live_exit_time","live_exit_avg_price","exit_quantity","buy_fee","sell_fee","sell_tax","realized_pnl","return_pct","capital_settled_at"))
        cursor.execute("""SELECT paper_trade_id,entry_signal_date,return_pct,data_segment
                         FROM daily_strategy_paper_trade WHERE strategy_id=%s AND entry_signal_date<DATE '2026-05-27'
                          AND entry_signal_date<=%s AND trade_status='CLOSED' AND return_pct IS NOT NULL
                         ORDER BY entry_signal_date DESC""", (strategy_id, as_of_date))
        historical = _dicts(cursor, ("paper_trade_id","entry_signal_date","return_pct","provenance"))
        cursor.execute("""SELECT source_bar_time AS timestamp, 'DAILY'::text AS timeframe,
                              event_kind AS signal_type, source_snapshot->>'direction' AS direction,
                              source_snapshot->>'entry_gap_pct' AS entry_gap_pct,
                              source_snapshot->>'exit_gap_pct' AS exit_gap_pct,
                              source_snapshot->>'trend_passed' AS trend_pass,
                              CASE WHEN source_bar_time::time=TIME '15:18' THEN 'FINAL' ELSE 'PROVISIONAL' END AS signal_phase,
                              outcome
                         FROM daily_strategy_paper_event
                        WHERE strategy_id=%s AND source_bar_time::date=%s
                        ORDER BY source_bar_time DESC,paper_event_id DESC LIMIT 100""", (strategy_id, as_of_date))
        signal_log = _dicts(cursor, ("timestamp","timeframe","signal_type","direction","entry_gap_pct","exit_gap_pct","trend_pass","signal_phase","outcome"))
        cursor.execute("""SELECT i.intent_id,i.intent_type,i.exit_reason,i.source_event_time,i.requested_quantity,
                              i.lifecycle_status AS intent_status,r.order_request_id,r.request_key,r.side,r.quantity,
                              r.request_status,b.broker_order_number,b.status AS broker_status,
                              COALESCE(f.filled_quantity,0) AS filled_quantity,
                              GREATEST(COALESCE(r.quantity,0)-COALESCE(f.filled_quantity,0),0) AS remaining_quantity,
                              cp.cumulative_filled_qty,cp.cumulative_filled_amount,cp.checkpoint_status
                         FROM daily_strategy_live_order_intent i
                         LEFT JOIN daily_strategy_live_order_request r USING(intent_id)
                         LEFT JOIN live_broker_order b USING(order_request_id)
                         LEFT JOIN (SELECT broker_order_id,sum(fill_quantity)::int AS filled_quantity FROM live_broker_fill GROUP BY broker_order_id) f USING(broker_order_id)
                         LEFT JOIN daily_strategy_live_fill_checkpoint cp USING(broker_order_id)
                        WHERE i.strategy_id=%s AND i.created_at::date=%s
                        ORDER BY i.created_at DESC""", (strategy_id, as_of_date))
        orders = _dicts(cursor, ("intent_id","intent_type","exit_reason","source_event_time","requested_quantity","intent_status","order_request_id","request_key","side","request_quantity","request_status","broker_order_number","broker_status","filled_quantity","remaining_quantity","cumulative_filled_qty","cumulative_filled_amount","checkpoint_status"))
        cursor.execute("""SELECT p.ownership_id,p.stock_code,p.quantity,p.average_cost,p.realized_pnl,p.last_fill_at,p.updated_at
                         FROM execution_logical_position p
                         JOIN daily_strategy_live_trade l ON l.ownership_id=p.ownership_id
                        WHERE l.strategy_id=%s AND p.ownership_type='LIVE' ORDER BY p.updated_at DESC""", (strategy_id,))
        ownership = _dicts(cursor, ("ownership_id","stock_code","quantity","average_cost","realized_pnl","last_fill_at","updated_at"))
        cursor.execute("""SELECT a.live_trade_id,a.allocation_side,a.fill_notional,a.allocated_buy_fee,a.allocated_sell_fee,
                              a.allocated_sell_tax,a.allocated_other_cost,a.rounding_residual_amount,s.trade_date,
                              s.execution_stock_code,s.finalization_status,s.finalized_at
                         FROM daily_strategy_live_broker_cost_allocation a
                         JOIN daily_strategy_live_trade l USING(live_trade_id)
                         JOIN daily_strategy_live_broker_cost_snapshot s USING(broker_cost_snapshot_id)
                        WHERE l.strategy_id=%s AND s.trade_date<=%s
                        ORDER BY s.trade_date DESC,a.live_trade_id,a.allocation_side""", (strategy_id, as_of_date))
        costs = _dicts(cursor, ("live_trade_id","allocation_side","fill_notional","allocated_buy_fee","allocated_sell_fee","allocated_sell_tax","allocated_other_cost","rounding_residual_amount","trade_date","execution_stock_code","finalization_status","finalized_at"))
        cursor.execute("""SELECT s.live_trade_id,s.capital_epoch_no,s.entry_filled_amount,s.exit_filled_amount,s.gross_realized_pnl,
                              s.buy_fee,s.sell_fee,s.sell_tax,s.other_cost_amount,s.net_realized_pnl,s.settled_at
                         FROM daily_strategy_live_capital_settlement s
                        WHERE s.strategy_id=%s AND s.settled_at::date<=%s ORDER BY s.settled_at DESC""", (strategy_id, as_of_date))
        settlements = _dicts(cursor, ("live_trade_id","capital_epoch_no","entry_filled_amount","exit_filled_amount","gross_realized_pnl","buy_fee","sell_fee","sell_tax","other_cost_amount","net_realized_pnl","settled_at"))
    current = next((row for row in grid_rows(pool, universe="ALL", as_of_date=as_of_date) if row["strategy_id"] == strategy_id), None)
    telemetry = {"daily_proximity": daily_proximity(pool, [current], as_of_date=as_of_date).get(strategy_id) if current else None,
                 "minute_telemetry": minute_telemetry(pool, [current], as_of_date=as_of_date).get(strategy_id) if current else None}
    return {"strategy_id": strategy_id, "as_of_date": as_of_date, "current": current, "paper_actual": paper, "live": live,
            "historical": historical, "signal_log": signal_log, "orders": orders, "ownership": ownership,
            "costs": costs, "settlements": settlements, **telemetry}
