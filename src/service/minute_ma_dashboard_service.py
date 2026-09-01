"""Read-only, paginated Minute-MA operating dashboard."""
from __future__ import annotations
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

SCOPES = {"V1_LIVE", "V1_ALL", "LEGACY"}
PAGE_SIZES = {20, 50}
PERIODS = {"DAILY", "WEEKLY", "MONTHLY", "ALL"}
RESEARCH_SOURCES = {"COMBINED", "HISTORICAL_REPLAY", "PAPER_FORWARD"}
LIFECYCLE_FILTERS = {
    "TODAY_LIVE_ENTRY", "TODAY_LIVE_EXIT", "OPEN_PAPER", "OVERNIGHT_PAPER", "STOP_PAPER",
    "POST", "ACK", "REJECT", "UNKNOWN", "FILL",
}
VIRTUAL_INITIAL_CAPITAL = Decimal("1000000")
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

def _period_window(as_of_date: date, period: str):
    period = period if period in PERIODS else "ALL"
    end = datetime.combine(as_of_date + timedelta(days=1), datetime.min.time())
    if period == "DAILY":
        start = datetime.combine(as_of_date, datetime.min.time())
    elif period == "WEEKLY":
        start = datetime.combine(as_of_date - timedelta(days=as_of_date.weekday()), datetime.min.time())
    elif period == "MONTHLY":
        start = datetime(as_of_date.year, as_of_date.month, 1)
    else:
        start = None
    return period, start, end

def _positive_period_frequency(trade_rows):
    """Count positive day/week/month buckets; empty calendar buckets do not exist."""
    buckets = {"day": defaultdict(list), "week": defaultdict(list), "month": defaultdict(list)}
    for row in trade_rows:
        exit_time, value = row[1], Decimal(row[2])
        buckets["day"][exit_time.date()].append(value)
        iso = exit_time.date().isocalendar()
        buckets["week"][(iso.year, iso.week)].append(value)
        buckets["month"][(exit_time.year, exit_time.month)].append(value)
    result = {}
    for label, grouped in buckets.items():
        positive = 0
        for returns in grouped.values():
            factor = Decimal("1")
            for value in returns:
                factor *= Decimal("1") + value / Decimal("100")
            positive += factor > Decimal("1")
        total = len(grouped)
        result[f"positive_{label}_count"] = positive
        result[f"evaluable_{label}_count"] = total
        result[f"positive_{label}_pct"] = (
            None if total == 0 else Decimal("100") * positive / total)
    return result

def _research_trade_rows(cursor, *, policy_path_ids, source, end):
    if not policy_path_ids:
        return []
    parts, params = [], []
    if source in {"COMBINED", "HISTORICAL_REPLAY"}:
        cursor.execute("SELECT to_regclass('public.minute_ma_policy_historical_trade')")
        if cursor.fetchone()[0] is not None:
            parts.append("""SELECT t.minute_policy_path_id,t.exit_execution_time,t.net_return_pct,
                 t.exit_reason,t.minute_policy_historical_trade_id::text source_order,
                 'HISTORICAL_REPLAY' provenance
              FROM minute_ma_policy_historical_trade t
              JOIN vw_minute_ma_v1_current_historical_run r USING(historical_run_id)
             WHERE t.minute_policy_path_id=ANY(%s) AND t.exit_execution_time<%s""")
            params.extend((policy_path_ids, end))
    if source in {"COMBINED", "PAPER_FORWARD"}:
        parts.append("""SELECT t.minute_policy_path_id,t.exit_execution_time,t.net_return_pct,
               t.exit_reason,t.minute_policy_paper_trade_id::text source_order,
               'PAPER_FORWARD' provenance
            FROM minute_ma_policy_paper_trade t
           WHERE t.minute_policy_path_id=ANY(%s) AND t.trade_status='CLOSED'
             AND t.exit_execution_time<%s""")
        params.extend((policy_path_ids, end))
    if not parts:
        return []
    cursor.execute(" UNION ALL ".join(parts) + " ORDER BY 1,2,5", tuple(params))
    return cursor.fetchall()

def _virtual_metrics(trade_rows, *, path_ids, start):
    grouped = defaultdict(list)
    for row in trade_rows:
        grouped[int(row[0])].append(row)
    result = {}
    for path_id in path_ids:
        before_factor = Decimal("1")
        period_factor = Decimal("1")
        wins = losses = stop = normal = 0
        cumulative_factor = Decimal("1")
        cumulative_wins = cumulative_losses = cumulative_stop = cumulative_normal = 0
        cumulative_returns = []
        cumulative_rows = []
        period_returns = []
        provenances = set()
        for _, exit_time, net_return, reason, _, provenance in grouped.get(path_id, ()):
            value = Decimal(net_return)
            factor = Decimal("1") + value / Decimal("100")
            cumulative_factor *= factor
            cumulative_returns.append(value)
            cumulative_rows.append((path_id, exit_time, value))
            cumulative_wins += value > 0
            cumulative_losses += value < 0
            cumulative_stop += reason == "STOP_EXIT"
            cumulative_normal += reason == "NORMAL_EXIT"
            provenances.add(provenance)
            if start is not None and exit_time < start:
                before_factor *= factor
                continue
            period_factor *= factor
            period_returns.append(value)
            wins += value > 0
            losses += value < 0
            stop += reason == "STOP_EXIT"
            normal += reason == "NORMAL_EXIT"
        start_capital = VIRTUAL_INITIAL_CAPITAL * before_factor
        end_capital = start_capital * period_factor
        count = len(period_returns)
        cumulative_count = len(cumulative_returns)
        cumulative_end_capital = VIRTUAL_INITIAL_CAPITAL * cumulative_factor
        result[path_id] = {
            "virtual_initial_capital": VIRTUAL_INITIAL_CAPITAL,
            "cumulative_end_capital": cumulative_end_capital,
            "cumulative_compound_profit": cumulative_end_capital - VIRTUAL_INITIAL_CAPITAL,
            "cumulative_compound_return_pct": (cumulative_factor - Decimal("1")) * Decimal("100"),
            "cumulative_closed_trade_count": cumulative_count,
            "cumulative_win_count": cumulative_wins,
            "cumulative_loss_count": cumulative_losses,
            "cumulative_win_rate_pct": None if cumulative_count == 0 else Decimal("100") * cumulative_wins / cumulative_count,
            "cumulative_avg_return_pct": None if cumulative_count == 0 else sum(cumulative_returns, Decimal("0")) / cumulative_count,
            "cumulative_worst_trade_pct": None if cumulative_count == 0 else min(cumulative_returns),
            "cumulative_stop_count": cumulative_stop,
            "cumulative_normal_exit_count": cumulative_normal,
            "period_start_capital": start_capital,
            "period_end_capital": end_capital,
            "period_compound_profit": end_capital - start_capital,
            "period_compound_return_pct": (period_factor - Decimal("1")) * Decimal("100"),
            "period_closed_trade_count": count, "period_win_count": wins,
            "period_loss_count": losses,
            "period_win_rate_pct": None if count == 0 else Decimal("100") * wins / count,
            "period_avg_return_pct": None if count == 0 else sum(period_returns, Decimal("0")) / count,
            "period_worst_trade_pct": None if count == 0 else min(period_returns),
            "period_stop_count": stop, "period_normal_exit_count": normal,
            "performance_provenance": sorted(provenances),
        }
        # Positive-period frequency is a cumulative research-quality metric through
        # as_of_date.  The Dashboard period selector only scopes period performance.
        result[path_id].update(_positive_period_frequency(cumulative_rows))
    ranked = sorted(result.items(), key=lambda item: (-item[1]["period_compound_return_pct"], item[0]))
    for rank, (path_id, _) in enumerate(ranked, 1):
        result[path_id]["period_rank"] = rank
    return result

def _positive_metrics_by_path(trade_rows, path_ids):
    grouped = defaultdict(list)
    for row in trade_rows:
        grouped[int(row[0])].append((row[0], row[1], Decimal(row[2])))
    return {path_id: _positive_period_frequency(grouped[path_id]) for path_id in path_ids}

def _v1_actual_metrics(cursor, policy_path_ids, *, start, end):
    if not policy_path_ids:
        return {}
    cursor.execute("""SELECT pp.minute_policy_path_id,
        cc.epoch_initial_capital,cc.strategy_compound_capital,
        cc.cumulative_net_realized_pnl
      FROM minute_ma_policy_path pp
      LEFT JOIN minute_ma_policy_operation po
        ON po.minute_policy_path_id=pp.minute_policy_path_id AND po.effective_to IS NULL
      LEFT JOIN minute_ma_policy_compound_capital cc
        ON cc.minute_policy_path_id=po.minute_policy_path_id AND cc.capital_epoch_no=po.capital_epoch_no
     WHERE pp.minute_policy_path_id=ANY(%s)
     GROUP BY pp.minute_policy_path_id,cc.epoch_initial_capital,
              cc.strategy_compound_capital,cc.cumulative_net_realized_pnl""", (policy_path_ids,))
    result = {}
    for path_id, initial, current, pnl in cursor.fetchall():
        result[int(path_id)] = {
            "actual_initial_capital": initial, "actual_current_capital": current,
            "actual_cumulative_pnl": pnl,
            "actual_compound_return_pct": None if initial in (None, 0) else Decimal("100") * Decimal(pnl or 0) / Decimal(initial),
            "actual_closed_trade_count": 0, "actual_win_rate_pct": None,
            "actual_period_start_capital": initial,
            "actual_period_end_capital": initial,
            "actual_period_pnl": Decimal("0"),
            "actual_period_return_pct": None if initial in (None, 0) else Decimal("0"),
            "actual_period_closed_count": 0, "actual_period_win_count": 0,
            "actual_period_loss_count": 0, "actual_period_win_rate_pct": None,
            "actual_period_avg_return_pct": None, "actual_period_worst_trade_pct": None,
            "actual_period_stop_count": 0, "actual_period_normal_exit_count": 0,
            "actual_positive_day_count": 0, "actual_evaluable_day_count": 0,
            "actual_positive_day_pct": None,
            "actual_positive_week_count": 0, "actual_evaluable_week_count": 0,
            "actual_positive_week_pct": None,
            "actual_positive_month_count": 0, "actual_evaluable_month_count": 0,
            "actual_positive_month_pct": None,
        }
    cursor.execute("""SELECT s.minute_policy_path_id,s.settled_at,s.net_realized_pnl,
        t.capital_at_signal,t.stop_trigger_time
      FROM minute_ma_live_capital_settlement s
      JOIN minute_ma_live_trade t USING(minute_live_trade_id)
     WHERE s.minute_policy_path_id=ANY(%s) AND s.settled_at<%s
     ORDER BY s.minute_policy_path_id,s.settled_at,s.settlement_id""",
      (policy_path_ids, end))
    settlements = defaultdict(list)
    for row in cursor.fetchall():
        settlements[int(row[0])].append(row)
    for path_id, metric in result.items():
        initial = metric["actual_initial_capital"]
        if initial is None:
            continue
        prior_pnl = period_pnl = Decimal("0")
        returns = []
        wins = losses = stops = 0
        all_count = all_wins = all_losses = 0
        for _, settled_at, pnl, capital_at_signal, stop_trigger_time in settlements[path_id]:
            pnl = Decimal(pnl)
            all_count += 1; all_wins += pnl > 0; all_losses += pnl < 0
            if start is not None and settled_at < start:
                prior_pnl += pnl
                continue
            period_pnl += pnl
            trade_return = Decimal("100") * pnl / Decimal(capital_at_signal)
            returns.append(trade_return)
            wins += pnl > 0; losses += pnl < 0; stops += stop_trigger_time is not None
        start_capital = Decimal(initial) + prior_pnl
        positive_actual = _positive_period_frequency([
            (path_id, settled_at, Decimal("100") * Decimal(pnl) / Decimal(capital_at_signal))
            for _, settled_at, pnl, capital_at_signal, _ in settlements[path_id]
        ])
        metric.update({
            "actual_closed_trade_count": all_count,
            "actual_win_count": all_wins,
            "actual_loss_count": all_losses,
            "actual_win_rate_pct": None if not all_count else Decimal("100") * all_wins / all_count,
            "actual_period_start_capital": start_capital,
            "actual_period_end_capital": start_capital + period_pnl,
            "actual_period_pnl": period_pnl,
            "actual_period_return_pct": None if start_capital == 0 else Decimal("100") * period_pnl / start_capital,
            "actual_period_closed_count": len(returns), "actual_period_win_count": wins,
            "actual_period_loss_count": losses,
            "actual_period_win_rate_pct": None if not returns else Decimal("100") * wins / len(returns),
            "actual_period_avg_return_pct": None if not returns else sum(returns, Decimal("0")) / len(returns),
            "actual_period_worst_trade_pct": None if not returns else min(returns),
            "actual_period_stop_count": stops,
            "actual_period_normal_exit_count": len(returns) - stops,
            **{f"actual_{key}": value for key, value in positive_actual.items()},
        })
    return result

def _v1_period_ranks(cursor, *, source, start, end):
    parts, params = [], []
    if source in {"COMBINED", "HISTORICAL_REPLAY"}:
        cursor.execute("SELECT to_regclass('public.minute_ma_policy_historical_trade')")
        if cursor.fetchone()[0] is not None:
            parts.append("""SELECT t.minute_policy_path_id,t.exit_execution_time,t.net_return_pct
              FROM minute_ma_policy_historical_trade t
              JOIN vw_minute_ma_v1_current_historical_run r USING(historical_run_id)
             WHERE t.exit_execution_time<%s""")
            params.append(end)
    if source in {"COMBINED", "PAPER_FORWARD"}:
        parts.append("""SELECT minute_policy_path_id,exit_execution_time,net_return_pct
          FROM minute_ma_policy_paper_trade
         WHERE trade_status='CLOSED' AND exit_execution_time<%s""")
        params.append(end)
    if not parts:
        return {}
    lower = "WHERE exit_execution_time>=%s" if start is not None else ""
    if start is not None:
        params.append(start)
    cursor.execute("""WITH trades AS (""" + " UNION ALL ".join(parts) + """),
      perf AS (
        SELECT minute_policy_path_id,
          100*(exp(sum(ln(1+net_return_pct/100)))-1) period_return
        FROM trades """ + lower + """ GROUP BY minute_policy_path_id
      ), ranked AS (
        SELECT pp.minute_policy_path_id,
          row_number() OVER(ORDER BY COALESCE(perf.period_return,0) DESC,
                                     pp.minute_policy_path_id)::int period_rank
        FROM minute_ma_policy_path pp LEFT JOIN perf USING(minute_policy_path_id)
        WHERE pp.is_enabled='Y'
      ) SELECT minute_policy_path_id,period_rank FROM ranked""", tuple(params))
    return {int(path_id): int(rank) for path_id, rank in cursor.fetchall()}

def _legacy_virtual_metrics(cursor, path_ids, *, start, end):
    if not path_ids:
        return {}
    cursor.execute("""SELECT minute_path_id,exit_execution_time,net_return_pct,exit_reason,
        minute_paper_trade_id::text,'PAPER_FORWARD'
      FROM minute_ma_paper_trade WHERE minute_path_id=ANY(%s) AND trade_status='CLOSED'
        AND exit_execution_time<%s ORDER BY minute_path_id,exit_execution_time,minute_paper_trade_id""",
        (path_ids, end))
    return _virtual_metrics(cursor.fetchall(), path_ids=path_ids, start=start)

def _operational(cursor, as_of_date):
    day_start = datetime.combine(as_of_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    cursor.execute("""SELECT
      (SELECT count(*) FROM minute_ma_path WHERE is_enabled='Y')::int total_paths,
      (SELECT count(*) FROM minute_ma_operation WHERE effective_to IS NULL AND operation_status='PAPER')::int paper_paths,
      (SELECT count(*) FROM minute_ma_operation WHERE effective_to IS NULL AND operation_status='LIVE')::int live_paths,
      (SELECT count(*) FROM minute_ma_policy_paper_trade
        WHERE entry_execution_time<%s AND (exit_execution_time IS NULL OR exit_execution_time>=%s))::int open_paper,
      (SELECT count(*) FROM minute_ma_live_trade WHERE trade_status='OPEN')::int open_live,
      (SELECT count(*) FROM minute_ma_policy_paper_event WHERE event_type='ENTRY' AND source_bar_time>=%s AND source_bar_time<%s)::int today_paper_entry,
      (SELECT count(*) FROM minute_ma_policy_paper_event WHERE event_type IN('NORMAL_EXIT','STOP_EXIT') AND source_bar_time>=%s AND source_bar_time<%s)::int today_paper_exit,
      (SELECT count(*) FROM minute_ma_live_signal_event WHERE minute_policy_path_id IS NOT NULL AND event_type='ENTRY' AND source_bar_time>=%s AND source_bar_time<%s)::int today_live_entry,
      (SELECT count(*) FROM minute_ma_live_signal_event WHERE minute_policy_path_id IS NOT NULL AND event_type='EXIT' AND source_bar_time>=%s AND source_bar_time<%s)::int today_live_exit,
      (SELECT count(DISTINCT minute_policy_path_id) FROM minute_ma_live_signal_event WHERE minute_policy_path_id IS NOT NULL AND event_type='ENTRY' AND source_bar_time>=%s AND source_bar_time<%s)::int today_live_entry_strategies,
      (SELECT count(DISTINCT minute_policy_path_id) FROM minute_ma_live_signal_event WHERE minute_policy_path_id IS NOT NULL AND event_type='EXIT' AND source_bar_time>=%s AND source_bar_time<%s)::int today_live_exit_strategies,
      (SELECT count(*) FROM minute_ma_live_broker_submit_attempt
        WHERE attempted_at>=%s AND attempted_at<%s)::int today_post_attempts,
      (SELECT count(*) FROM live_broker_order b JOIN minute_ma_live_order_link l USING(broker_order_id)
        JOIN minute_ma_live_intent i USING(intent_id)
        WHERE i.minute_policy_path_id IS NOT NULL AND b.created_at>=%s AND b.created_at<%s AND b.status IN('ACCEPTED','PARTIALLY_FILLED','FILLED'))::int today_acknowledged,
      (SELECT count(*) FROM live_broker_order b JOIN minute_ma_live_order_link l USING(order_request_id)
        JOIN minute_ma_live_intent i USING(intent_id)
        WHERE i.minute_policy_path_id IS NOT NULL AND b.created_at>=%s AND b.created_at<%s AND b.status='REJECTED')::int today_rejected,
      (SELECT count(*) FROM live_broker_order b JOIN minute_ma_live_order_link l USING(order_request_id)
        JOIN minute_ma_live_intent i USING(intent_id)
        WHERE i.minute_policy_path_id IS NOT NULL AND b.created_at>=%s AND b.created_at<%s AND b.status='UNKNOWN_BROKER_STATE')::int today_unknown,
      (SELECT count(*) FROM live_broker_order b JOIN minute_ma_live_order_link l USING(broker_order_id)
        JOIN minute_ma_live_intent i USING(intent_id)
        WHERE i.minute_policy_path_id IS NOT NULL AND b.created_at>=%s AND b.created_at<%s)::int today_orders,
      (SELECT COALESCE(sum(a.delta_quantity),0) FROM minute_ma_live_checkpoint_allocation a
        JOIN minute_ma_live_trade t USING(minute_live_trade_id)
        WHERE t.minute_policy_path_id IS NOT NULL AND a.created_at>=%s AND a.created_at<%s)::int today_filled_qty,
      (SELECT count(*) FROM minute_ma_live_entry_skip WHERE minute_policy_path_id IS NOT NULL AND created_at>=%s AND created_at<%s)::int today_skips,
      (SELECT send_enabled FROM minute_ma_send_profile WHERE profile_code='MINUTE_MA_LIVE_SEND') send_enabled,
      (SELECT max(updated_at) FROM minute_ma_runtime_cursor)::timestamp last_runtime_at""",
      (day_end, day_end) + tuple([day_start, day_end] * 13))
    return dict(zip([d.name for d in cursor.description], cursor.fetchone()))

def _recent_rejections(cursor, limit=10):
    cursor.execute("""SELECT s.source_daily_strategy_id strategy_id,s.signal_code,s.execution_code,
        s.direction,b.quantity,b.payload->>'order_policy' order_policy,x.kis_tr_id,x.kis_endpoint,
        x.response_code,x.response_message,x.rejected_at,x.evidence_type
      FROM minute_ma_live_broker_rejection x
      JOIN live_broker_order b ON b.broker_order_id=x.broker_order_id
      JOIN minute_ma_live_order_link l ON l.order_request_id=x.order_request_id
      JOIN minute_ma_live_intent i ON i.intent_id=x.intent_id
      JOIN minute_ma_policy_path pp ON pp.minute_policy_path_id=i.minute_policy_path_id
      JOIN minute_ma_path p ON p.minute_path_id=pp.minute_path_id
      JOIN minute_ma_strategy_master s USING(minute_strategy_id)
     ORDER BY x.rejected_at DESC,x.broker_order_id DESC LIMIT %s""",(limit,))
    return _dicts(cursor)

def _v1_summary(cursor, as_of_date):
    day_end = datetime.combine(as_of_date + timedelta(days=1), datetime.min.time())
    cursor.execute("""SELECT count(*)::int policy_paths,
      count(*) FILTER(WHERE proposed_initial_capital IS NOT NULL)::int candidates,
      COALESCE(sum(proposed_initial_capital),0) proposed_capital,
      count(*) FILTER(WHERE v1_selection_status='SELECTED')::int selected_paths,
      count(*) FILTER(WHERE v1_operation_status='LIVE')::int live_paths,
      count(*) FILTER(WHERE v1_strategy_compound_capital IS NOT NULL)::int capital_epochs,
      COALESCE(sum(v1_strategy_compound_capital),0) strategy_compound_capital,
      (SELECT count(*) FROM minute_ma_policy_paper_trade
        WHERE entry_execution_time<%s AND (exit_execution_time IS NULL OR exit_execution_time>=%s))::int open_trades,
      (SELECT count(*) FROM minute_ma_policy_paper_trade
        WHERE entry_execution_time::date<%s AND entry_execution_time<%s
          AND (exit_execution_time IS NULL OR exit_execution_time>=%s))::int overnight_open,
      (SELECT count(*) FROM minute_ma_policy_paper_trade WHERE trade_status='CLOSED' AND exit_reason='STOP_EXIT' AND exit_execution_time<%s)::int stop_exits
      FROM vw_minute_ma_v1_policy_dashboard""",
      (day_end, day_end, as_of_date, day_end, day_end, day_end))
    return dict(zip([d.name for d in cursor.description], cursor.fetchone()))

def _lifecycle_predicate(lifecycle_filter, as_of_date):
    if lifecycle_filter not in LIFECYCLE_FILTERS:
        return None, []
    start = datetime.combine(as_of_date, datetime.min.time())
    end = start + timedelta(days=1)
    predicates = {
        "TODAY_LIVE_ENTRY": ("EXISTS (SELECT 1 FROM minute_ma_live_signal_event e WHERE e.minute_policy_path_id=d.minute_policy_path_id AND e.event_type='ENTRY' AND e.source_bar_time>=%s AND e.source_bar_time<%s)", [start, end]),
        "TODAY_LIVE_EXIT": ("EXISTS (SELECT 1 FROM minute_ma_live_signal_event e WHERE e.minute_policy_path_id=d.minute_policy_path_id AND e.event_type='EXIT' AND e.source_bar_time>=%s AND e.source_bar_time<%s)", [start, end]),
        "OPEN_PAPER": ("EXISTS (SELECT 1 FROM minute_ma_policy_paper_trade t WHERE t.minute_policy_path_id=d.minute_policy_path_id AND t.entry_execution_time<%s AND (t.exit_execution_time IS NULL OR t.exit_execution_time>=%s))", [end, end]),
        "OVERNIGHT_PAPER": ("EXISTS (SELECT 1 FROM minute_ma_policy_paper_trade t WHERE t.minute_policy_path_id=d.minute_policy_path_id AND t.entry_execution_time::date<%s AND t.entry_execution_time<%s AND (t.exit_execution_time IS NULL OR t.exit_execution_time>=%s))", [as_of_date, end, end]),
        "STOP_PAPER": ("EXISTS (SELECT 1 FROM minute_ma_policy_paper_trade t WHERE t.minute_policy_path_id=d.minute_policy_path_id AND t.trade_status='CLOSED' AND t.exit_reason='STOP_EXIT' AND t.exit_execution_time<%s)", [end]),
        "POST": ("EXISTS (SELECT 1 FROM minute_ma_live_broker_submit_attempt a JOIN minute_ma_live_intent i USING(intent_id) WHERE i.minute_policy_path_id=d.minute_policy_path_id AND a.attempted_at>=%s AND a.attempted_at<%s)", [start, end]),
        "REJECT": ("EXISTS (SELECT 1 FROM minute_ma_live_broker_rejection r JOIN minute_ma_live_intent i USING(intent_id) WHERE i.minute_policy_path_id=d.minute_policy_path_id AND r.rejected_at>=%s AND r.rejected_at<%s)", [start, end]),
        "ACK": ("EXISTS (SELECT 1 FROM minute_ma_live_intent i JOIN minute_ma_live_order_link l USING(intent_id) JOIN live_broker_order b USING(broker_order_id) WHERE i.minute_policy_path_id=d.minute_policy_path_id AND b.created_at>=%s AND b.created_at<%s AND b.status IN('ACCEPTED','PARTIALLY_FILLED','FILLED'))", [start, end]),
        "UNKNOWN": ("EXISTS (SELECT 1 FROM minute_ma_live_intent i JOIN minute_ma_live_order_link l USING(intent_id) JOIN live_broker_order b USING(order_request_id) WHERE i.minute_policy_path_id=d.minute_policy_path_id AND b.created_at>=%s AND b.created_at<%s AND b.status='UNKNOWN_BROKER_STATE')", [start, end]),
        "FILL": ("EXISTS (SELECT 1 FROM minute_ma_live_trade t JOIN minute_ma_live_checkpoint_allocation a USING(minute_live_trade_id) WHERE t.minute_policy_path_id=d.minute_policy_path_id AND a.created_at>=%s AND a.created_at<%s)", [start, end]),
    }
    return predicates[lifecycle_filter]

def _v1_page(cursor, *, scope, page, page_size, sort, search, direction,
             lifecycle_filter=None, as_of_date=None):
    conditions, params = [], []
    if scope == "V1_LIVE":
        conditions.append("v1_operation_status='LIVE'")
    if direction in {"LONG", "SHORT"}:
        conditions.append("direction=%s"); params.append(direction)
    if search:
        conditions.append("(source_daily_strategy_id ILIKE %s OR signal_code ILIKE %s OR execution_code ILIKE %s)")
        term = f"%{search[:80]}%"; params.extend((term, term, term))
    predicate, lifecycle_params = _lifecycle_predicate(lifecycle_filter, as_of_date or date.today())
    if predicate:
        conditions.append(predicate); params.extend(lifecycle_params)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    cursor.execute("SELECT count(*)::int FROM vw_minute_ma_v1_policy_dashboard d" + where, tuple(params))
    total = cursor.fetchone()[0]
    order = V1_SORTS.get(sort, V1_SORTS["rank"])
    cursor.execute("SELECT d.* FROM vw_minute_ma_v1_policy_dashboard d" + where +
                   f" ORDER BY {order} LIMIT %s OFFSET %s",
                   tuple(params + [page_size, (page - 1) * page_size]))
    rows = _dicts(cursor)
    for row in rows:
        row["top20_consecutive_days"] = row.get("current_top20_consecutive_days", row.get("top20_consecutive_days", 0))
    return rows, total

def _v1_period_lifecycle(cursor, policy_path_ids, *, start, end):
    """Return trade lifecycle counts without mixing OPEN trades into realized P&L."""
    if not policy_path_ids:
        return {}
    paper_entry_lower = "entry_execution_time>=%s AND " if start is not None else ""
    paper_exit_lower = "exit_execution_time>=%s AND " if start is not None else ""
    paper_params = ([start] if start is not None else []) + [end]
    paper_params += ([start] if start is not None else []) + [end]
    paper_params += ([start] if start is not None else []) + [end, end, policy_path_ids, end]
    cursor.execute("""SELECT minute_policy_path_id,
        count(*) FILTER(WHERE """ + paper_entry_lower + """entry_execution_time<%s)::int period_paper_entry_count,
        count(*) FILTER(WHERE exit_execution_time IS NOT NULL
                              AND """ + paper_exit_lower + """exit_execution_time<%s)::int period_paper_exit_count,
        count(*) FILTER(WHERE """ + paper_entry_lower + """entry_execution_time<%s
                              AND (exit_execution_time IS NULL OR exit_execution_time>=%s))::int period_paper_open_count
      FROM minute_ma_policy_paper_trade
     WHERE minute_policy_path_id=ANY(%s) AND entry_execution_time<%s
     GROUP BY minute_policy_path_id""",
      tuple(paper_params))
    result = {int(row[0]): {
        "period_paper_entry_count": row[1], "period_paper_exit_count": row[2],
        "period_paper_open_count": row[3],
        "period_live_entry_count": 0, "period_live_exit_count": 0,
        "period_live_open_count": 0,
    } for row in cursor.fetchall()}
    live_entry_lower = "t.created_at>=%s AND " if start is not None else ""
    live_exit_lower = "s.settled_at>=%s AND " if start is not None else ""
    live_params = ([start] if start is not None else []) + [end]
    live_params += ([start] if start is not None else []) + [end]
    live_params += ([start] if start is not None else []) + [end, end, policy_path_ids, end]
    cursor.execute("""SELECT t.minute_policy_path_id,
        count(*) FILTER(WHERE """ + live_entry_lower + """t.created_at<%s)::int period_live_entry_count,
        count(*) FILTER(WHERE s.settled_at IS NOT NULL
                              AND """ + live_exit_lower + """s.settled_at<%s)::int period_live_exit_count,
        count(*) FILTER(WHERE t.trade_status<>'CANCELLED'
                              AND """ + live_entry_lower + """t.created_at<%s
                              AND (s.settled_at IS NULL OR s.settled_at>=%s))::int period_live_open_count
      FROM minute_ma_live_trade t
      LEFT JOIN minute_ma_live_capital_settlement s USING(minute_live_trade_id)
     WHERE t.minute_policy_path_id=ANY(%s) AND t.created_at<%s
     GROUP BY t.minute_policy_path_id""",
      tuple(live_params))
    for path_id, entries, exits, opens in cursor.fetchall():
        metric = result.setdefault(int(path_id), {
            "period_paper_entry_count": 0, "period_paper_exit_count": 0,
            "period_paper_open_count": 0,
        })
        metric.update({"period_live_entry_count": entries,
                       "period_live_exit_count": exits,
                       "period_live_open_count": opens})
    for path_id in policy_path_ids:
        result.setdefault(int(path_id), {
            "period_paper_entry_count": 0, "period_paper_exit_count": 0,
            "period_paper_open_count": 0, "period_live_entry_count": 0,
            "period_live_exit_count": 0, "period_live_open_count": 0,
        })
    return result

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
                      page_size=20, sort=None, search=None, direction=None,
                      as_of_date=None, period="ALL", performance_source="COMBINED",
                      lifecycle_filter=None) -> dict:
    """Return one requested page; default entry is only the 20 V1 LIVE paths."""
    scope = scope if scope in SCOPES else "V1_LIVE"
    page, page_size = _page(page), _page_size(page_size)
    search = (search or "").strip()
    as_of_date = as_of_date or date.today()
    if isinstance(as_of_date, str):
        as_of_date = date.fromisoformat(as_of_date)
    period, period_start, period_end = _period_window(as_of_date, period)
    performance_source = performance_source if performance_source in RESEARCH_SOURCES else "COMBINED"
    lifecycle_filter = lifecycle_filter if lifecycle_filter in LIFECYCLE_FILTERS else None
    with pool.connection() as c, c.cursor() as q:
        operational = _operational(q, as_of_date)
        recent_rejections = _recent_rejections(q)
        q.execute("SELECT to_regclass('public.vw_minute_ma_v1_policy_dashboard')")
        has_v1 = q.fetchone()[0] is not None
        v1_summary = _v1_summary(q, as_of_date) if has_v1 else {
            "policy_paths": 0, "candidates": 0, "proposed_capital": 0, "selected_paths": 0,
            "live_paths": 0, "capital_epochs": 0, "strategy_compound_capital": 0,
            "open_trades": 0, "overnight_open": 0, "stop_exits": 0}
        if scope.startswith("V1"):
            rows, total = _v1_page(q, scope=scope, page=page, page_size=page_size,
                                   sort=sort, search=search, direction=direction,
                                   lifecycle_filter=lifecycle_filter,
                                   as_of_date=as_of_date) if has_v1 else ([], 0)
            ids = [int(row["minute_policy_path_id"]) for row in rows]
            research = _virtual_metrics(
                _research_trade_rows(q, policy_path_ids=ids, source=performance_source, end=period_end),
                path_ids=ids, start=period_start)
            # Positive-period frequency has one official scope: cumulative
            # HISTORICAL_REPLAY + PAPER_FORWARD CLOSED through as_of_date.
            if performance_source != "COMBINED":
                combined_positive = _positive_metrics_by_path(
                    _research_trade_rows(q, policy_path_ids=ids, source="COMBINED", end=period_end), ids)
                for path_id in ids:
                    research[path_id].update(combined_positive[path_id])
            actual = _v1_actual_metrics(q, ids, start=period_start, end=period_end)
            lifecycle = _v1_period_lifecycle(q, ids, start=period_start, end=period_end)
            period_ranks = _v1_period_ranks(
                q, source=performance_source, start=period_start, end=period_end)
            for row in rows:
                path_id = int(row["minute_policy_path_id"])
                row.update(research[path_id]); row["period_rank"] = period_ranks.get(path_id)
                row.update(actual.get(path_id, {}))
                row.update(lifecycle[path_id])
        else:
            rows, total = _legacy_page(q, axis=axis, operation=operation, page=page,
                                       page_size=page_size, sort=sort, search=search,
                                       direction=direction)
            ids = [int(row["minute_path_id"]) for row in rows]
            research = _legacy_virtual_metrics(q, ids, start=period_start, end=period_end)
            for row in rows:
                row.update(research[int(row["minute_path_id"])])
    return {"status": "OK", "scope": scope, "operational": operational,
            "recent_rejections": recent_rejections,
            "v1_summary": v1_summary, "rows": rows, "row_count": len(rows),
            "page": page, "page_size": page_size, "total_count": total,
            "total_pages": max(1, (total + page_size - 1) // page_size),
            "as_of_date": as_of_date, "period": period,
            "period_from": period_start.date() if period_start else None,
            "period_to": as_of_date, "performance_source": performance_source,
            "lifecycle_filter": lifecycle_filter,
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
