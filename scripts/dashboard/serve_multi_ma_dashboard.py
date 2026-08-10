"""읽기 전용 다중 MA 대시보드: JSON은 원자적으로 갱신하고 HTTP는 localhost에만 바인딩한다."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, time as clock_time, timedelta
from decimal import Decimal
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from src.repository.database import DatabaseSettings, create_connection_pool
from scripts.admin.serve_research_backfill_admin import PAGE as RESEARCH_BACKFILL_PAGE, application as run_research_backfill
from src.service.research_performance_projection import aggregate as aggregate_projected, project_cycle
from src.analysis.feature.sma_feature import MinuteBar
from src.service.provisional_daily_observation_service import RawMinute, observe as observe_provisional_daily

KST = ZoneInfo("Asia/Seoul")


def _json_default(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _research_stock_names(cursor) -> dict[str, str]:
    """Load STOCK labels once per read-only research response.

    This deliberately uses the stored common-code name only: no KIS lookup and
    no per-cycle query are involved.
    """
    cursor.execute("""SELECT code, NULLIF(BTRIM(code_name), '')
                    FROM common_code WHERE group_cd='STOCK'""")
    return {str(code): str(name) for code, name in cursor.fetchall() if name is not None}


def _korean_naive_timestamp(value: datetime) -> datetime:
    """Normalise database timestamp adapters before feature-time comparison."""
    return value.astimezone(KST).replace(tzinfo=None) if value.tzinfo is not None else value


def research_daily_intraday_payload(pool, query: dict[str, list[str]]) -> dict:
    """Read stored RAW only for the daily research page's transient observer.

    The response does not write research features/signals/cycles and therefore
    cannot affect historical COMPLETE research rankings or daily RAW.
    """
    period = max(1, int((query.get("ma_period") or ["10"])[0]))
    strategy = (query.get("strategy_code") or ["SIGNAL_1"])[0]
    direction = (query.get("direction") or ["ALL"])[0]
    condition = (query.get("entry_condition") or ["MA_CONFIRM_INTEGRATED"])[0]
    trade_filter = (query.get("trade_stock_code") or ["ALL"])[0]
    source_filter = (query.get("signal_source_stock_code") or ["ALL"])[0]
    now = datetime.now(KST).replace(tzinfo=None)
    today = now.date()
    start_of_day = datetime.combine(today, clock_time.min)
    # Never read a future timestamp even if malformed/future RAW happened to
    # exist for the current calendar date.
    end_of_day = min(start_of_day + timedelta(days=1), now)
    with pool.connection() as conn, conn.cursor() as cur:
        stock_names = _research_stock_names(cur)
        # STOCK_DAILY is the only target source.  STOCK.attr7 is used solely
        # to select the already configured minute RAW venue for that target.
        cur.execute("""SELECT daily.code, daily.code_name,
                              COALESCE(NULLIF(BTRIM(stock.attr7), ''), NULLIF(BTRIM(daily.attr7), ''), 'KRX')
                       FROM common_code daily
                       LEFT JOIN common_code stock ON stock.group_cd='STOCK' AND stock.code=daily.code
                      WHERE daily.group_cd='STOCK_DAILY' AND daily.use_yn='Y'
                      ORDER BY daily.sort_order, daily.code""")
        targets = cur.fetchall()
        items = []
        for stock_code, stock_name, minute_venue in targets:
            stock_code = str(stock_code)
            # No implicit trade/source mapping is introduced here: this
            # observer has one target/source pair per STOCK_DAILY target.
            if trade_filter not in ("ALL", stock_code) or source_filter not in ("ALL", stock_code):
                continue
            cur.execute("""SELECT trade_date,open_price,high_price,low_price,close_price,volume
                             FROM raw_stock_daily
                            WHERE stock_code=%s AND data_source='KIS' AND trading_venue='KRX'
                              AND collect_cycle='DAILY' AND trade_date < %s
                              AND close_price IS NOT NULL
                            ORDER BY trade_date DESC LIMIT %s""",
                        (stock_code, today, max(20, period)))
            historical_rows = list(reversed(cur.fetchall()))
            history = [MinuteBar(datetime.combine(row[0], clock_time(15, 19)), row[1], row[2], row[3], row[4])
                       for row in historical_rows]
            cur.execute("""SELECT trade_date,open_price,high_price,low_price,close_price,volume
                             FROM raw_stock_daily
                            WHERE stock_code=%s AND data_source='KIS' AND trading_venue='KRX'
                              AND collect_cycle='DAILY' AND trade_date=%s
                              AND close_price IS NOT NULL""",
                        (stock_code, today))
            official = cur.fetchone()
            official_bar = (MinuteBar(datetime.combine(today, clock_time(15, 19)), official[1], official[2], official[3], official[4])
                            if official else None)
            cur.execute("""SELECT bar_time,open_price,high_price,low_price,close_price,volume
                             FROM raw_stock_minute
                            WHERE stock_code=%s AND data_source='KIS' AND trading_venue=%s AND collect_cycle='1MIN'
                              AND bar_time >= %s AND bar_time < %s AND close_price IS NOT NULL
                            ORDER BY bar_time""",
                        (stock_code, minute_venue, start_of_day, end_of_day))
            minutes = [RawMinute(_korean_naive_timestamp(row[0]), row[1], row[2], row[3], row[4], row[5] or Decimal("0"))
                       for row in cur.fetchall()]
            item = observe_provisional_daily(stock_code=stock_code, daily_history=history, minute_rows=minutes,
                                              official_today=official_bar, period=period, strategy_code=strategy,
                                              entry_condition=condition, direction=direction)
            item.update({"trade_stock_code": stock_code, "signal_source_stock_code": stock_code,
                         "stock_name": stock_names.get(stock_code) or stock_name or stock_code,
                         "ma_period": period})
            if official:
                item["volume"] = official[5]
            items.append(item)
    return {"status": "OK", "trading_date": today, "items": items, "stock_names": stock_names}


def _analysis_session_id(bar_time: datetime) -> str | None:
    value = bar_time.time()
    if clock_time(8, 0) <= value <= clock_time(8, 49, 59):
        return "NXT_PREMARKET"
    if clock_time(9, 0) <= value <= clock_time(15, 19, 59):
        return "KRX_REGULAR"
    if clock_time(15, 40) <= value <= clock_time(20, 0):
        return "NXT_AFTERMARKET"
    return None


def _contiguous_average(points, period: int):
    """Average only an exact, consecutive one-minute window.

    Missing official bars must never be silently skipped to manufacture an MA.
    This applies to the dashboard JSON as well as the runtime analyser.
    """
    if len(points) < period:
        return None
    window = points[-period:]
    for previous, current in zip(window, window[1:]):
        if current[0] - previous[0] != timedelta(minutes=1):
            return None
    return round(sum(value for _time, value in window) / period, 2)


def build_program_minute_series(rows):
    """Use the last raw snapshot in each minute; never manufacture a zero value."""
    result, previous_time, previous_value, previous_session = [], None, None, None
    for row in rows:
        minute_time = row["minute_time"]
        session = _analysis_session_id(minute_time)
        if session is None:
            continue
        status, interval = "NORMAL", None
        if session != previous_session:
            status = "SESSION_START"
        elif previous_time is None or minute_time - previous_time != timedelta(minutes=1):
            status = "PROGRAM_DATA_GAP"
        else:
            interval = float(row["cumulative_net_buy_amount"]) - float(previous_value)
        result.append({**row, "minute_time": minute_time, "source_snapshot_time": row["source_snapshot_time"],
                       "minute_net_buy_amount": interval, "status": status})
        previous_time, previous_value, previous_session = minute_time, row["cumulative_net_buy_amount"], session
    return result


def dashboard_payload(pool) -> dict:
    """대시보드에 필요한 읽기 전용 데이터만 반환한다. 실패는 호출자가 격리한다."""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT bar_time, stock_code, trading_venue, close_price FROM raw_stock_minute
        WHERE stock_code='000660' AND trading_venue='INTEGRATED' AND collect_cycle='1MIN'
        ORDER BY bar_time DESC LIMIT 1""")
        completed = cur.fetchone()
        cur.execute("""SELECT snapshot_time,target_bar_time,stock_code,trading_venue,close_price FROM raw_stock_minute_snapshot
        WHERE stock_code='000660' AND trading_venue='INTEGRATED' AND collect_cycle='5SEC'
        ORDER BY snapshot_time DESC LIMIT 1""")
        snapshot = cur.fetchone()
        cur.execute("""SELECT bar_time,open_price,high_price,low_price,close_price,volume FROM raw_stock_minute WHERE stock_code='000660'
        AND trading_venue='INTEGRATED' AND collect_cycle='1MIN' AND bar_time::date=CURRENT_DATE ORDER BY bar_time""")
        completed_series = cur.fetchall()
        cur.execute("""SELECT snapshot_time,target_bar_time,open_price,high_price,low_price,close_price,volume FROM raw_stock_minute_snapshot WHERE stock_code='000660'
        AND trading_venue='INTEGRATED' AND collect_cycle='5SEC' AND snapshot_time::date=CURRENT_DATE ORDER BY snapshot_time""")
        snapshot_series = cur.fetchall()
        cur.execute("""SELECT DISTINCT ON (date_trunc('minute', snapshot_time))
        date_trunc('minute', snapshot_time) minute_time, snapshot_time, collected_at,
        execution_strength, execution_volume
        FROM raw_stock_execution WHERE stock_code='000660' AND trading_venue='INTEGRATED'
        AND collect_cycle='5SEC' AND snapshot_time::date=CURRENT_DATE
        ORDER BY date_trunc('minute', snapshot_time), collected_at DESC, snapshot_time DESC""")
        execution_minutes = cur.fetchall()
        cur.execute("""SELECT DISTINCT ON (date_trunc('minute', snapshot_time))
        date_trunc('minute', snapshot_time) minute_time, snapshot_time, sell_amount, buy_amount,
        net_buy_amount, net_buy_volume, net_buy_amount_change
        FROM raw_program WHERE stock_code='000660' AND market_code='KOSPI' AND collect_cycle='1MIN'
        AND snapshot_time::date=CURRENT_DATE
        ORDER BY date_trunc('minute', snapshot_time), snapshot_time DESC""")
        program_minutes = cur.fetchall()
        cur.execute("""SELECT strategy_code,observation_code,position_direction,position_weight,last_processed_time
        FROM analysis_multi_ma_state WHERE stock_code='000660' AND trading_venue='INTEGRATED'
        ORDER BY strategy_code,observation_code""")
        states = cur.fetchall()
        cur.execute("""SELECT strategy_code,observation_code,total_profit_amount,total_profit_rate,trade_count,
        win_count,loss_count,win_rate,signal_exit_count,session_close_exit_count,
        signal_exit_profit,session_close_exit_profit,max_profit,max_loss
        FROM analysis_multi_ma_summary
        WHERE stock_code='000660' AND trading_venue='INTEGRATED' AND trade_date=CURRENT_DATE
        ORDER BY total_profit_rate DESC""")
        summaries = cur.fetchall()
        cur.execute("""SELECT DISTINCT ON (strategy_code,observation_code,signal_time,signal_no,direction)
        signal_id,signal_time,signal_no,direction,signal_price,observation_code,strategy_code,reason
        FROM analysis_multi_ma_signal WHERE stock_code='000660' AND trading_venue='INTEGRATED' AND trade_date=CURRENT_DATE
        ORDER BY strategy_code,observation_code,signal_time,signal_no,direction,signal_id""")
        signals = cur.fetchall()
        cur.execute("""SELECT trade_id,cycle_no,entry_time,direction,entry_price,entry_ratio,average_entry_price,
        exit_time,exit_price,exit_type,exit_reason,realized_profit_amount,realized_profit_rate,
        strategy_code,observation_code,status
        FROM analysis_multi_ma_trade WHERE stock_code='000660' AND trading_venue='INTEGRATED' AND trade_date=CURRENT_DATE
        ORDER BY entry_time""")
        trades = cur.fetchall()
        cur.execute("""SELECT leg.trade_id,leg.signal_no,leg.signal_time,leg.entry_price,leg.entry_ratio,
        leg.notional_amount FROM analysis_multi_ma_trade_leg leg
        JOIN analysis_multi_ma_trade trade ON trade.trade_id=leg.trade_id
        WHERE trade.stock_code='000660' AND trade.trading_venue='INTEGRATED' AND trade.trade_date=CURRENT_DATE
        ORDER BY leg.trade_id,leg.signal_time""")
        legs = cur.fetchall()
    columns = lambda names, rows: [dict(zip(names, row)) for row in rows]
    now = datetime.now(KST)
    completed_values = [(row[0], float(row[4])) for row in completed_series]
    program_rows = build_program_minute_series(columns(("minute_time", "source_snapshot_time", "cumulative_sell_amount", "cumulative_buy_amount", "cumulative_net_buy_amount", "cumulative_net_buy_volume", "api_net_buy_amount_change"), program_minutes))
    execution_rows = columns(("minute_time", "source_snapshot_time", "collected_at", "execution_strength", "execution_volume"), execution_minutes)
    previous_execution_time = previous_strength = previous_session = None
    for row in execution_rows:
        session = _analysis_session_id(row["minute_time"])
        if session is None:
            row["status"], row["previous_execution_strength"], row["execution_strength_change"] = "DATA_MISSING", None, None
            continue
        continuous = session == previous_session and previous_execution_time is not None and row["minute_time"] - previous_execution_time == timedelta(minutes=1)
        row["status"] = "NORMAL" if continuous else ("SESSION_START" if session != previous_session else "EXECUTION_STRENGTH_GAP")
        row["previous_execution_strength"] = previous_strength if continuous else None
        row["execution_strength_change"] = (float(row["execution_strength"]) - float(previous_strength)) if continuous and row["execution_strength"] is not None and previous_strength is not None else None
        previous_execution_time, previous_strength, previous_session = row["minute_time"], row["execution_strength"], session
    def point(timestamp, price):
        # timestamp is represented exactly once: a completed bar replaces the
        # in-progress value for COMPLETE, while SEC observations append only
        # their current target minute after prior completed bars.
        session_id = _analysis_session_id(timestamp)
        values = [
            (at, value) for at, value in completed_values
            if at < timestamp and _analysis_session_id(at) == session_id
        ]
        values.append((timestamp, float(price)))
        return {
            "timestamp": timestamp,
            "price": price,
            "ma_short": _contiguous_average(values, 3),
            "ma_mid": _contiguous_average(values, 5),
            "ma_long": _contiguous_average(values, 10),
            "ma20": _contiguous_average(values, 20),
        }
    series = {"COMPLETE": [point(at, value) for at, value in completed_values]}
    # The main chart intentionally has one shared official history.  Each
    # observation may replace only the current, not-yet-completed minute.
    current_observations: dict[str, dict] = {}
    latest_completed_time = completed_values[-1][0] if completed_values else None
    for second in range(5, 60, 5):
        code = f"SEC_{second:02d}"
        series[code] = [point(row[1], row[5]) for row in snapshot_series if row[0].second == second]
        candidates = [row for row in snapshot_series if row[0].second == second
                      and (latest_completed_time is None or row[1] > latest_completed_time)]
        if candidates:
            snapshot_time, target_bar_time, _open, _high, _low, price, _volume = candidates[-1]
            current_observations[code] = {
                "snapshot_time": snapshot_time,
                "target_bar_time": target_bar_time,
                "price": price,
            }
    signal_rows = columns(("signal_id", "signal_time", "signal_no", "direction", "signal_price", "observation_code", "strategy_code", "reason"), signals)
    by_minute: dict[str, dict] = {}
    for bar_time, open_price, high_price, low_price, close_price, volume in completed_series:
        feature = point(bar_time, close_price)
        by_minute[bar_time.isoformat()] = {
            "bar_time": bar_time,
            "official": {
                "open_price": open_price, "high_price": high_price,
                "low_price": low_price, "close_price": close_price,
                "volume": volume, **feature,
            },
            "observations": {},
        }
    for snapshot_time, target_bar_time, open_price, high_price, low_price, close_price, volume in snapshot_series:
        second = snapshot_time.second
        if second not in range(5, 60, 5):
            continue
        detail = by_minute.setdefault(target_bar_time.isoformat(), {
            "bar_time": target_bar_time, "official": None, "observations": {},
        })
        code = f"SEC_{second:02d}"
        detail["observations"][code] = {
            "observation_time": snapshot_time,
            "open_price": open_price, "high_price": high_price,
            "low_price": low_price, "close_price": close_price,
            "volume": volume, **point(target_bar_time, close_price),
        }
    for detail in by_minute.values():
        if detail["official"] is not None:
            detail["observations"]["COMPLETE"] = {
                "observation_time": detail["bar_time"],
                **detail["official"],
            }
        for code, observation in detail["observations"].items():
            matched = [
                signal for signal in signal_rows
                if signal["observation_code"] == code and signal["signal_time"] == detail["bar_time"]
            ]
            # ACCUMULATED stores the same canonical event for its own
            # independent strategy.  Detail display is signal-type oriented,
            # so do not render the same event twice merely because two
            # strategies consumed it.
            unique: dict[tuple, dict] = {}
            for signal in matched:
                unique.setdefault((signal["signal_time"], signal["signal_no"], signal["direction"]), signal)
            observation["canonical_signals"] = list(unique.values())
        program = next((row for row in program_rows if row["minute_time"] == detail["bar_time"]), None)
        detail["program"] = program or {"status": "DATA_MISSING"}
        execution = next((row for row in execution_rows if row["minute_time"] == detail["bar_time"]), None)
        detail["execution_strength"] = execution or {"status": "DATA_MISSING"}
    in_market = now.weekday() < 5 and now.time().strftime("%H:%M") >= "08:00" and now.time().strftime("%H:%M") <= "20:05"
    status = "DATA_MISSING" if in_market and (completed is None or snapshot is None) else ("OPEN" if in_market else "CLOSED")
    return {
        "generated_at": now,
        "market_status": status,
        "strategy_alert_enabled": False,
        "order_enabled": False,
        "latest_completed": None if completed is None else dict(zip(("bar_time","stock_code","trading_venue","close_price"), completed)),
        "latest_snapshot": None if snapshot is None else dict(zip(("snapshot_time","target_bar_time","stock_code","trading_venue","close_price"), snapshot)),
        "completed_count_today": len(completed_series), "snapshot_count_today": len(snapshot_series),
        "main_completed_series": series["COMPLETE"], "current_observations": current_observations,
        "program_minutes": program_rows, "programMinuteSeries": program_rows,
        "programStatus": "NORMAL" if program_rows else "DATA_MISSING",
        "executionStrengthSeries": execution_rows,
        "executionStrengthStatus": "NORMAL" if execution_rows else "DATA_MISSING",
        "series": series, "minute_details": list(by_minute.values()),
        "states": columns(("strategy_code","observation_code","position_direction","position_weight","last_processed_time"), states),
        "summaries": columns(("strategy_code","observation_code","total_profit_amount","total_profit_rate","trade_count","win_count","loss_count","win_rate","signal_exit_count","session_close_exit_count","signal_exit_profit","session_close_exit_profit","max_profit","max_loss"), summaries),
        "signals": signal_rows,
        "trades": columns(("trade_id","cycle_no","entry_time","direction","entry_price","entry_ratio","average_entry_price","exit_time","exit_price","exit_type","exit_reason","realized_profit_amount","realized_profit_rate","strategy_code","observation_code","status"), trades),
        "trade_legs": columns(("trade_id","signal_no","signal_time","entry_price","entry_ratio","notional_amount"), legs),
    }


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, default=_json_default), encoding="utf-8")
    os.replace(temporary, path)


def _research_filters(
    query: dict[str, list[str]],
    alias: str = "c",
    *,
    entry_condition: str | None = None,
    include_direction: bool = True,
) -> tuple[str, list]:
    """Build only whitelisted, parameterized filters over stored closed cycles."""
    fields = {
        "trade_stock_code": "trade_stock_code", "signal_source_stock_code": "signal_source_stock_code",
        "strategy_code": "strategy_code", "observation_code": "observation_code", "direction": "direction",
    }
    where, params = [], []
    for key, column in fields.items():
        if key == "direction" and not include_direction:
            continue
        value = (query.get(key) or ["ALL"])[0]
        if value and value != "ALL":
            where.append(f"{alias}.{column}=%s"); params.append(value)
    start, end = (query.get("start_date") or [None])[0], (query.get("end_date") or [None])[0]
    if start: where.append(f"{alias}.trading_date >= %s"); params.append(start)
    if end: where.append(f"{alias}.trading_date <= %s"); params.append(end)
    session = (query.get("analysis_session") or ["ALL_INTEGRATED"])[0]
    session_ranges = {
        "NXT_PRE": ("08:00", "08:50"),
        "REGULAR": ("09:00", "15:20"),
        "NXT_AFTER": ("15:40", "20:01"),
    }
    if session in session_ranges:
        lower, upper = session_ranges[session]
        where.append(f"{alias}.entry_time::time >= %s::time AND {alias}.entry_time::time < %s::time")
        params.extend((lower, upper))
    elif session == "REGULAR_AFTER_AGGREGATED":
        where.append(f"(({alias}.entry_time::time >= '09:00'::time AND {alias}.entry_time::time < '15:20'::time) OR ({alias}.entry_time::time >= '15:40'::time AND {alias}.entry_time::time < '20:01'::time))")
    if entry_condition == "MA10_READY_AT_SIGNAL":
        where.append(f"{alias}.entry_signal_time = {alias}.entry_confirm_time")
    return (" AND " + " AND ".join(where) if where else ""), params


def _research_run_condition(entry_condition: str) -> str:
    """Map a display-only entry mode to the persisted replay run policy."""
    return "MA10_CONFIRM" if entry_condition in {"MA10_READY_AT_SIGNAL", "MA_CONFIRM", "MA_CONFIRM_INTEGRATED"} else entry_condition


def research_performance_payload(pool, query: dict[str, list[str]]) -> dict:
    """Read-only dynamic research performance; never uses deprecated period rows."""
    condition = (query.get("entry_condition") or ["MA10_CONFIRM"])[0]
    if condition not in {"SIGNAL_ONLY", "MA10_READY_AT_SIGNAL", "MA10_CONFIRM"}:
        condition = "MA10_CONFIRM"
    run_condition = _research_run_condition(condition)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT run_id,start_date,end_date,parameters FROM research_run
                       WHERE status='COMPLETED' AND parameters->>'entry_condition'=%s
                       ORDER BY end_date DESC,created_at DESC LIMIT 1""", (run_condition,))
        run = cur.fetchone()
        if run is None:
            return {"entry_condition": condition, "status": "NO_COMPLETED_RUN", "summary": {}, "daily": [], "ranking": [], "comparison": {}}
        run_id, start_date, end_date, parameters = run
        where, values = _research_filters(query, entry_condition=condition)
        base = "FROM research_trade_cycle c WHERE c.run_id=%s AND c.exit_time IS NOT NULL" + where
        cur.execute("""SELECT count(*),count(*) FILTER(WHERE realized_profit>0),count(*) FILTER(WHERE realized_profit<0),count(*) FILTER(WHERE realized_profit=0),
                         coalesce(sum(realized_profit),0),coalesce(sum(invested_amount),0),coalesce(sum(gross_realized_profit),0),
                         coalesce(sum(total_trading_cost),0),coalesce(avg(invested_return_rate),0),coalesce(avg(holding_seconds),0),
                         coalesce(sum(realized_profit) FILTER(WHERE exit_type='SIGNAL'),0),coalesce(sum(realized_profit) FILTER(WHERE exit_type='SESSION_CLOSE'),0)
                       """ + base, [run_id, *values])
        summary = dict(zip(("closed_count","win_count","loss_count","flat_count","realized_profit","invested_amount","gross_realized_profit","total_trading_cost","avg_trade_return_rate","avg_holding_seconds","signal_exit_profit","session_close_profit"), cur.fetchone()))
        summary["win_rate"] = 0 if not summary["closed_count"] else summary["win_count"] / summary["closed_count"] * 100
        summary["invested_return_rate"] = 0 if not summary["invested_amount"] else summary["realized_profit"] / summary["invested_amount"] * 100
        summary["capital_return_rate"] = summary["realized_profit"] / 10000000 * 100
        summary["gross_invested_return_rate"] = 0 if not summary["invested_amount"] else summary["gross_realized_profit"] / summary["invested_amount"] * 100
        summary["gross_capital_return_rate"] = summary["gross_realized_profit"] / 10000000 * 100
        daily_where, daily_values = _research_filters(query, "c", entry_condition=condition)
        cur.execute("""SELECT c.trading_date,c.trade_stock_code,c.signal_source_stock_code,c.strategy_code,c.observation_code,c.direction,
                         max(p.daily_return_rate),max(p.daily_market_direction),
                         count(*),count(*) FILTER(WHERE c.realized_profit>0),count(*) FILTER(WHERE c.realized_profit<0),count(*) FILTER(WHERE c.realized_profit=0),
                         coalesce(sum(c.gross_realized_profit),0),coalesce(sum(c.total_trading_cost),0),coalesce(sum(c.realized_profit),0),coalesce(sum(c.invested_amount),0),
                         coalesce(sum(c.gross_realized_profit)/nullif(sum(c.invested_amount),0)*100,0),
                         coalesce(sum(c.realized_profit)/nullif(sum(c.invested_amount),0)*100,0),
                         coalesce(sum(c.gross_realized_profit)/10000000*100,0),coalesce(sum(c.realized_profit)/10000000*100,0),
                         coalesce(avg(c.invested_return_rate),0),coalesce(avg(c.holding_seconds),0),
                         coalesce(sum(c.realized_profit) FILTER(WHERE c.exit_type='SIGNAL'),0),coalesce(sum(c.realized_profit) FILTER(WHERE c.exit_type='SESSION_CLOSE'),0)
                       FROM research_trade_cycle c
                       LEFT JOIN research_performance_daily p ON p.run_id=c.run_id AND p.trading_date=c.trading_date
                         AND p.trade_stock_code=c.trade_stock_code AND p.signal_source_stock_code=c.signal_source_stock_code
                         AND p.strategy_code=c.strategy_code AND p.observation_code=c.observation_code AND p.direction=c.direction
                       WHERE c.run_id=%s AND c.exit_time IS NOT NULL""" + daily_where + " GROUP BY c.trading_date,c.trade_stock_code,c.signal_source_stock_code,c.strategy_code,c.observation_code,c.direction ORDER BY c.trading_date DESC LIMIT 500", [run_id, *daily_values])
        names = ("trading_date","trade_stock_code","signal_source_stock_code","strategy_code","observation_code","direction","daily_return_rate","daily_market_direction","closed_count","win_count","loss_count","flat_count","gross_realized_profit","total_trading_cost","realized_profit","invested_amount","gross_invested_return_rate","invested_return_rate","gross_capital_return_rate","capital_return_rate","avg_trade_return_rate","avg_holding_seconds","signal_exit_profit","session_close_profit")
        daily = [dict(zip(names, row)) for row in cur.fetchall()]
        cur.execute("""SELECT trade_stock_code,signal_source_stock_code,strategy_code,observation_code,direction,count(*),count(*) FILTER(WHERE realized_profit>0),count(*) FILTER(WHERE realized_profit<0),
                         coalesce(sum(gross_realized_profit),0),coalesce(sum(total_trading_cost),0),coalesce(sum(realized_profit),0),coalesce(sum(invested_amount),0),
                         coalesce(sum(gross_realized_profit)/nullif(sum(invested_amount),0)*100,0),coalesce(sum(realized_profit)/nullif(sum(invested_amount),0)*100,0),
                         coalesce(sum(gross_realized_profit)/10000000*100,0),coalesce(sum(realized_profit)/10000000*100,0),
                         coalesce(avg(invested_return_rate),0),coalesce(avg(holding_seconds),0)
                       """ + base + " GROUP BY trade_stock_code,signal_source_stock_code,strategy_code,observation_code,direction ORDER BY 15 DESC,11 DESC LIMIT 20", [run_id, *values])
        rank_names = ("trade_stock_code","signal_source_stock_code","strategy_code","observation_code","direction","closed_count","win_count","loss_count","gross_realized_profit","total_trading_cost","realized_profit","invested_amount","gross_invested_return_rate","invested_return_rate","gross_capital_return_rate","capital_return_rate","avg_trade_return_rate","avg_holding_seconds")
        ranking = [dict(zip(rank_names, row)) for row in cur.fetchall()]
        comparison = {}
        for candidate in ("SIGNAL_ONLY", "MA10_READY_AT_SIGNAL", "MA10_CONFIRM"):
            cur.execute("""SELECT run_id FROM research_run WHERE status='COMPLETED' AND start_date=%s AND end_date=%s AND parameters->>'entry_condition'=%s ORDER BY created_at DESC LIMIT 1""", (start_date, end_date, _research_run_condition(candidate)))
            other = cur.fetchone()
            if other:
                candidate_where, candidate_values = _research_filters(query, entry_condition=candidate)
                cur.execute("""SELECT count(*),count(*) FILTER(WHERE realized_profit>0),count(*) FILTER(WHERE realized_profit<0),
                               coalesce(sum(gross_realized_profit),0),coalesce(sum(total_trading_cost),0),coalesce(sum(realized_profit),0)
                               FROM research_trade_cycle c WHERE c.run_id=%s AND c.exit_time IS NOT NULL""" + candidate_where, [other[0], *candidate_values])
                closed, wins, losses, gross_profit, total_cost, profit = cur.fetchone()
                comparison[candidate] = {"run_id": other[0], "closed_count": closed, "win_count": wins, "loss_count": losses,
                                          "win_rate": 0 if not closed else wins / closed * 100,
                                          "gross_realized_profit": gross_profit, "total_trading_cost": total_cost, "realized_profit": profit,
                                          "gross_capital_return_rate": gross_profit / 10000000 * 100,
                                          "capital_return_rate": profit / 10000000 * 100}
    return {"status": "OK", "run_id": run_id, "start_date": start_date, "end_date": end_date, "entry_condition": condition,
            "parameters": parameters, "summary": summary, "daily": daily, "ranking": ranking, "comparison": comparison}


def _projection_rates(parameters: dict, stock_code: str) -> tuple[Decimal, Decimal]:
    policy = (parameters or {}).get("cost_policy") or {}
    prefix = "stock" if stock_code == "000660" else "etf_etn"
    return Decimal(str(policy.get(f"{prefix}_fee_rate", "0"))), Decimal(str(policy.get(f"{prefix}_sell_tax_rate", "0")))


def _projected_cycles(cur, run_id, parameters: dict, query: dict[str, list[str]], condition: str) -> list[dict]:
    where, values = _research_filters(query, entry_condition=condition, include_direction=False)
    cur.execute("""SELECT c.cycle_id,c.trading_date,c.trade_stock_code,c.signal_source_stock_code,c.exit_signal_source_stock_code,c.strategy_code,c.observation_code,c.direction,
      c.entry_signal_time,c.entry_confirm_time,c.entry_time,c.entry_price,c.exit_time,c.exit_price,c.exit_type,c.holding_seconds,
      COALESCE(json_agg(json_build_object('entry_price',l.entry_price,'entry_ratio',l.entry_ratio) ORDER BY l.entry_time)
      FILTER (WHERE l.cycle_id IS NOT NULL), '[]'::json) legs
      FROM research_trade_cycle c LEFT JOIN research_trade_leg l ON l.cycle_id=c.cycle_id
      WHERE c.run_id=%s AND c.exit_time IS NOT NULL""" + where + " GROUP BY c.cycle_id ORDER BY c.entry_time", [run_id, *values])
    names = ("cycle_id","trading_date","trade_stock_code","signal_source_stock_code","exit_signal_source_stock_code","strategy_code","observation_code","direction","entry_signal_time","entry_confirm_time","entry_time","entry_price","exit_time","exit_price","exit_type","holding_seconds","legs")
    rows = [dict(zip(names, item)) for item in cur.fetchall()]
    trade_set = (query.get("trade_set") or ["ALL"])[0]
    allowed = {
        "STOCK_LONG": {("000660", "000660", "LONG")},
        "BIDIRECTIONAL_LEVERAGE": {("0193T0", "000660", "LONG"), ("0197X0", "000660", "LONG")},
        "MIXED_STOCK_INVERSE": {("000660", "000660", "LONG"), ("0197X0", "000660", "LONG")},
    }.get(trade_set)
    if allowed is not None:
        rows = [row for row in rows if (row["trade_stock_code"],row["signal_source_stock_code"],row["direction"]) in allowed]
    direction_filter = (query.get("direction") or ["ALL"])[0]
    if direction_filter not in {"ALL", "LONG", "SHORT"}:
        direction_filter = "ALL"
    for row in rows:
        row["trade_set"] = trade_set
        # direction is the persisted individual-cycle enum.  Trade sets and
        # future bidirectional modes must not rewrite it for a read-only UI.
        row["analysis_direction"] = row["direction"]
    if direction_filter != "ALL":
        rows = [row for row in rows if row["analysis_direction"] == direction_filter]
    limit = (query.get("trade_limit") or ["ALL"])[0]
    if limit in {"1", "3", "5", "10"}:
        count, selected = {}, []
        for row in rows:
            key = (row["trading_date"],row["strategy_code"],trade_set); count[key] = count.get(key, 0) + 1
            if count[key] <= int(limit): selected.append(row)
        rows = selected
    timeframe = (query.get("timeframe") or [(parameters or {}).get("timeframe", "MINUTE")])[0]
    if timeframe not in {"MINUTE", "DAILY"}: timeframe = "MINUTE"
    result = []
    for row in rows:
        fee, tax = _projection_rates(parameters, row["trade_stock_code"])
        result.append(project_cycle(row, fee_rate=fee, sell_tax_rate=tax, timeframe=timeframe))
    return result


def _projected_groups(rows: list[dict], fields: tuple[str, ...]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for row in rows: groups.setdefault(tuple(row[field] for field in fields), []).append(row)
    output = []
    for key, values in groups.items():
        item = dict(zip(fields, key)); item.update(aggregate_projected(values)); output.append(item)
    return output


def _daily_group_key(row: dict) -> tuple:
    return tuple(row.get(field) for field in (
        "trade_stock_code", "signal_source_stock_code", "strategy_code", "observation_code", "direction"
    ))


def _apply_daily_open_valuation(
    ranking: list[dict], daily: list[dict], position_rows: list[dict], initial_capital: Decimal,
) -> tuple[list[dict], list[dict], dict]:
    """Attach persisted daily mark-to-market values without replaying cycles.

    Every amount remains isolated by the complete strategy/source combination;
    only the UI decides whether a single combination may be shown as an account.
    """
    daily_open: dict[tuple, Decimal] = {}
    for row in position_rows:
        key = _daily_group_key(row)
        profit = Decimal(str(row.get("unrealized_profit") or 0))
        daily_open[(row["trading_date"], key)] = daily_open.get((row["trading_date"], key), Decimal("0")) + profit

    # Include dates that have an open mark but no closed cycle.  The daily
    # table then shows the requested mark-to-market path rather than hiding it
    # until another cycle happens to close.
    existing = {(row["trading_date"], _daily_group_key(row)) for row in daily}
    for row in position_rows:
        key = (row["trading_date"], _daily_group_key(row))
        if key not in existing:
            day, group = key
            daily.append({
                "trading_date": day,
                "trade_stock_code": group[0], "signal_source_stock_code": group[1],
                "strategy_code": group[2], "observation_code": group[3], "direction": group[4],
                "daily_return_rate": None, "daily_market_direction": None,
                "closed_count": 0, "win_count": 0, "loss_count": 0, "flat_count": 0,
                "gross_realized_profit": Decimal("0"), "total_trading_cost": Decimal("0"),
                "realized_profit": Decimal("0"), "invested_amount": Decimal("0"),
                "gross_invested_return_rate": Decimal("0"), "invested_return_rate": Decimal("0"),
                "gross_capital_return_rate": Decimal("0"), "capital_return_rate": Decimal("0"),
                "avg_trade_return_rate": Decimal("0"), "avg_holding_seconds": Decimal("0"),
                "signal_exit_profit": Decimal("0"), "session_close_profit": Decimal("0"),
            })
            existing.add(key)

    # The latest row per cycle is selected by the caller.  Aggregate it per
    # combination here, preserving multiple independent open combinations.
    latest_open: dict[tuple, Decimal] = {}
    for row in position_rows:
        key = _daily_group_key(row)
        latest_open[key] = latest_open.get(key, Decimal("0")) + Decimal(str(row.get("unrealized_profit") or 0)) if row.get("is_latest") else latest_open.get(key, Decimal("0"))
    # Ranking begins with closed cycles.  Add OPEN-only combinations before
    # attaching valuation so they participate in rank, overview, and UI.
    ranked_keys = {_daily_group_key(item) for item in ranking}
    for row in position_rows:
        key = _daily_group_key(row)
        if key not in ranked_keys:
            item = dict(zip(("trade_stock_code", "signal_source_stock_code", "strategy_code", "observation_code", "direction"), key))
            item.update(aggregate_projected([]))
            ranking.append(item)
            ranked_keys.add(key)
    for item in ranking:
        open_profit = latest_open.get(_daily_group_key(item), Decimal("0"))
        realized = Decimal(str(item.get("realized_profit") or 0))
        item["initial_capital"] = initial_capital
        item["open_valuation_profit"] = open_profit
        item["total_valuation_profit"] = realized + open_profit
        item["realized_capital_return_rate"] = Decimal("0") if not initial_capital else realized / initial_capital * 100
        item["total_valuation_return_rate"] = Decimal("0") if not initial_capital else item["total_valuation_profit"] / initial_capital * 100

    cumulative: dict[tuple, Decimal] = {}
    for item in sorted(daily, key=lambda row: (row["trading_date"], _daily_group_key(row))):
        key = _daily_group_key(item)
        cumulative[key] = cumulative.get(key, Decimal("0")) + Decimal(str(item.get("realized_profit") or 0))
        open_profit = daily_open.get((item["trading_date"], key), Decimal("0"))
        item["initial_capital"] = initial_capital
        item["realized_cumulative_profit"] = cumulative[key]
        item["realized_capital_return_rate"] = Decimal("0") if not initial_capital else cumulative[key] / initial_capital * 100
        item["open_valuation_profit"] = open_profit
        item["total_valuation_profit"] = cumulative[key] + open_profit
        item["total_valuation_return_rate"] = Decimal("0") if not initial_capital else item["total_valuation_profit"] / initial_capital * 100

    rates = [Decimal(str(item["total_valuation_return_rate"])) for item in ranking]
    total_rates = [Decimal(str(item["total_valuation_return_rate"])) for item in ranking]
    median = lambda values: Decimal("0") if not values else sorted(values)[(len(values) - 1) // 2]
    overview = {
        "combination_count": len(ranking),
        "profitable_combination_count": sum(rate > 0 for rate in rates),
        "losing_combination_count": sum(rate < 0 for rate in rates),
        "best_realized_capital_return_rate": max(rates, default=Decimal("0")),
        "median_realized_capital_return_rate": median(rates),
        "best_total_valuation_return_rate": max(total_rates, default=Decimal("0")),
        "median_total_valuation_return_rate": median(total_rates),
    }
    daily.sort(key=lambda row: row["trading_date"], reverse=True)
    return ranking, daily, overview


def research_performance_payload_v2(pool, query: dict[str, list[str]]) -> dict:
    """Read-only target-capital comparison; no replay, RAW access, or writes."""
    condition = (query.get("entry_condition") or ["MA10_CONFIRM"])[0]
    if condition not in {"SIGNAL_ONLY","MA10_READY_AT_SIGNAL","MA10_CONFIRM","MA_CONFIRM","MA_CONFIRM_INTEGRATED","MA_AT_SIGNAL"}: condition = "MA10_CONFIRM"
    timeframe = (query.get("timeframe") or ["MINUTE"])[0]
    if timeframe not in {"MINUTE", "DAILY"}: timeframe = "MINUTE"
    session = (query.get("analysis_session") or ["ALL_INTEGRATED"])[0]
    session_mode = "REGULAR_AFTER_CONTINUOUS" if session == "REGULAR_AFTER_CONTINUOUS" else None
    with pool.connection() as conn, conn.cursor() as cur:
        stock_names = _research_stock_names(cur)
        run_sql = """SELECT run_id,start_date,end_date,parameters,initial_capital FROM research_run WHERE status='COMPLETED'
          AND COALESCE(parameters->>'timeframe','MINUTE')=%s"""
        run_values = [timeframe]
        if timeframe == "DAILY":
            run_sql += " AND parameters->>'warmup_policy'='TRADING_BARS_V2_DYNAMIC_MA_PERIOD'"
        if timeframe == "DAILY" and condition == "MA_CONFIRM_INTEGRATED":
            # MA10_CONFIRM/MA_CONFIRM are read-only compatibility aliases for
            # completed runs written before the official daily policy name.
            run_sql += " AND parameters->>'entry_condition' IN ('MA_CONFIRM_INTEGRATED','MA_CONFIRM','MA10_CONFIRM')"
        else:
            run_sql += " AND parameters->>'entry_condition'=%s"
            run_values.append(_research_run_condition(condition))
        if session_mode:
            run_sql += " AND parameters->>'session_mode'=%s"; run_values.append(session_mode)
        else:
            run_sql += " AND COALESCE(parameters->>'session_mode','SEPARATE')='SEPARATE'"
        if timeframe == "DAILY" and condition in {"MA_CONFIRM", "MA_CONFIRM_INTEGRATED", "MA_AT_SIGNAL"}:
            run_sql += " AND COALESCE(parameters->>'ma_period','10')=%s"
            run_values.append((query.get("ma_period") or ["10"])[0])
        requested_start, requested_end = (query.get("start_date") or [""])[0], (query.get("end_date") or [""])[0]
        if timeframe == "DAILY" and requested_start:
            # The UI period is an aggregation window, not a replay identity.
            # Select a completed run that covers it, preferring the narrowest
            # covering interval and then the newest completed run.
            run_sql += " AND start_date <= %s"; run_values.append(requested_start)
        if timeframe == "DAILY" and requested_end:
            run_sql += " AND end_date >= %s"; run_values.append(requested_end)
        if timeframe == "DAILY" and (requested_start or requested_end):
            run_sql += " ORDER BY (end_date - start_date) ASC, created_at DESC LIMIT 1"
        else:
            run_sql += " ORDER BY end_date DESC,created_at DESC LIMIT 1"
        cur.execute(run_sql, run_values)
        run = cur.fetchone()
        if run is None: return {"status":"NO_COMPLETED_RUN","entry_condition":condition,"summary":{},"daily":[],"ranking":[],"comparison":{},"stock_names":stock_names}
        run_id,start_date,end_date,parameters,initial_capital = run
        # A completed long-range run can contain hundreds of thousands of
        # cycles.  Keep the first interactive view bounded; an explicit date
        # selection always wins and remains a full dynamic period query.
        effective_query = {key: list(value) for key, value in query.items()}
        requested_start = (effective_query.get("start_date") or [""])[0]
        requested_end = (effective_query.get("end_date") or [""])[0]
        if not (requested_start or requested_end):
            effective_start = max(start_date, end_date - timedelta(days=6))
            effective_query["start_date"] = [effective_start.isoformat()]
            effective_query["end_date"] = [end_date.isoformat()]
        else:
            effective_start = requested_start or start_date.isoformat()
            effective_query["start_date"] = [effective_start]
            effective_query["end_date"] = [requested_end or end_date.isoformat()]
        rows = _projected_cycles(cur, run_id, parameters, effective_query, condition)
        summary = aggregate_projected(rows)
        daily = _projected_groups(rows, ("trading_date","trade_stock_code","signal_source_stock_code","strategy_code","observation_code","analysis_direction"))
        for item in daily:
            item["direction"] = item.pop("analysis_direction"); item["daily_return_rate"] = None; item["daily_market_direction"] = None
        daily.sort(key=lambda item: item["trading_date"], reverse=True)
        ranking = _projected_groups(rows, ("trade_stock_code","signal_source_stock_code","strategy_code","observation_code","analysis_direction"))
        for item in ranking: item["direction"] = item.pop("analysis_direction")
        comparison = {}
        comparison_modes = ("SIGNAL_ONLY", "MA_CONFIRM") if timeframe == "DAILY" else ("SIGNAL_ONLY","MA10_READY_AT_SIGNAL","MA10_CONFIRM")
        for candidate in comparison_modes:
            compare_sql = """SELECT run_id,parameters FROM research_run WHERE status='COMPLETED' AND start_date=%s AND end_date=%s
              AND parameters->>'entry_condition'=%s AND COALESCE(parameters->>'timeframe','MINUTE')=%s"""
            compare_values = [start_date,end_date,_research_run_condition(candidate),timeframe]
            if session_mode: compare_sql += " AND parameters->>'session_mode'=%s"; compare_values.append(session_mode)
            else: compare_sql += " AND COALESCE(parameters->>'session_mode','SEPARATE')='SEPARATE'"
            if timeframe == "DAILY" and candidate == "MA_CONFIRM":
                compare_sql += " AND COALESCE(parameters->>'ma_period','10')=%s"
                compare_values.append((query.get("ma_period") or ["10"])[0])
            compare_sql += " ORDER BY created_at DESC LIMIT 1"
            cur.execute(compare_sql, compare_values)
            other = cur.fetchone()
            if other: comparison[candidate] = aggregate_projected(_projected_cycles(cur, other[0], other[1], effective_query, candidate))
        open_positions, open_position_daily = [], []
        if timeframe == "DAILY":
            open_where, open_values = _research_filters(effective_query, entry_condition=condition)
            cur.execute("""SELECT c.cycle_id,c.trade_stock_code,c.signal_source_stock_code,c.exit_signal_source_stock_code,c.strategy_code,c.observation_code,c.direction,
                c.entry_time,c.entry_price,p.trading_date,p.valuation_close_price,p.quantity,p.invested_amount,p.unrealized_profit,p.unrealized_return_rate,
                row_number() OVER (PARTITION BY p.cycle_id ORDER BY p.trading_date DESC)=1 AS is_latest
              FROM research_trade_cycle c JOIN research_position_daily p ON p.cycle_id=c.cycle_id
              WHERE c.run_id=%s AND c.exit_time IS NULL AND p.trading_date <= %s""" + open_where + " ORDER BY p.cycle_id,p.trading_date", [run_id, effective_query["end_date"][0], *open_values])
            names=("cycle_id","trade_stock_code","signal_source_stock_code","exit_signal_source_stock_code","strategy_code","observation_code","direction","entry_time","entry_price","trading_date","valuation_close_price","quantity","invested_amount","unrealized_profit","unrealized_return_rate","is_latest")
            open_position_daily=[dict(zip(names,row)) for row in cur.fetchall()]
            open_positions=[]
            for row in open_position_daily:
                if row["is_latest"]:
                    item=dict(row); item["valuation_date"]=item.pop("trading_date")
                    item["holding_days"] = (item["valuation_date"] - item["entry_time"].date()).days
                    open_positions.append(item)
            ranking, daily, valuation_overview = _apply_daily_open_valuation(ranking, daily, open_position_daily, Decimal(str(initial_capital)))
        else:
            valuation_overview = {}
        # OPEN-only combinations are added by _apply_daily_open_valuation, so
        # sort/limit only after that union.  Total valuation is the daily
        # research rank criterion, not closed-cycle invested return.
        ranking.sort(key=lambda item: (item.get("total_valuation_return_rate", item["invested_return_rate"]), item["total_valuation_profit"]), reverse=True)
        # Research grids sort the complete filtered result client-side; their
        # fixed-height containers provide the visual 20-row viewport.
        rank_limit = (query.get("rank_limit") or ["20"])[0]
        if rank_limit != "ALL":
            ranking = ranking[:int(rank_limit if rank_limit in {"10", "20"} else "20")]
    return {"status":"OK","run_id":run_id,"start_date":start_date,"end_date":end_date,"entry_condition":condition,
            "selected_start_date":effective_query["start_date"][0],"selected_end_date":effective_query["end_date"][0],
            "timeframe":timeframe,"parameters":parameters,"initial_capital":initial_capital,"summary":summary,"daily":daily,"ranking":ranking,"comparison":comparison,"open_positions":open_positions if timeframe == "DAILY" else [],"valuation_overview":valuation_overview,"stock_names":stock_names}


def research_cycle_payload(pool, query: dict[str, list[str]]) -> dict:
    run_id = (query.get("run_id") or [None])[0]
    if not run_id:
        return {"cycles": [], "stock_names": {}}
    condition = (query.get("entry_condition") or ["MA10_CONFIRM"])[0]
    with pool.connection() as conn, conn.cursor() as cur:
        stock_names = _research_stock_names(cur)
        cur.execute("SELECT parameters FROM research_run WHERE run_id=%s", (run_id,))
        found = cur.fetchone()
        if found is None:
            return {"cycles": [], "stock_names": stock_names}
        rows = _projected_cycles(cur, run_id, found[0], query, condition)
        rows.sort(key=lambda item: item["entry_time"], reverse=True)
        # Detail grids request ALL explicitly so a header sort is applied to
        # the complete filtered set, not only a visible subset.
        limit = (query.get("limit") or ["1000"])[0]
        return {"cycles": rows if limit == "ALL" else rows[:1000], "stock_names": stock_names}


class DashboardHandler(SimpleHTTPRequestHandler):
    pool = None

    def end_headers(self):
        # The dashboard shell changes independently from the JSON payload.
        # Never let a browser retain a malformed or stale index.html after a
        # deployment; JSON polling already carries its own cache-busting key.
        request_path = self.path.split("?", 1)[0]
        if request_path in ("/", "/index.html"):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(403, "Directory listing disabled")
        return None
    def log_message(self, format, *args):
        logging.info("http %s", format % args)
    def _read_only(self):
        self.send_error(405, "Read-only dashboard")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/admin/backfill":
            body = RESEARCH_BACKFILL_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/research/performance/api":
            try:
                body = json.dumps(research_performance_payload_v2(self.pool, parse_qs(parsed.query)), ensure_ascii=False, default=_json_default).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
            except Exception as error:
                logging.exception("research performance query failed")
                body = json.dumps({"status": "ERROR", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False).encode("utf-8")
                self.send_response(500); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)
            return
        if parsed.path == "/research/daily/api":
            try:
                query = parse_qs(parsed.query)
                # This endpoint is deliberately isolated from minute research.
                query["timeframe"] = ["DAILY"]
                body = json.dumps(research_performance_payload_v2(self.pool, query), ensure_ascii=False, default=_json_default).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
            except Exception as error:
                logging.exception("daily research query failed")
                body = json.dumps({"status": "ERROR", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False).encode("utf-8")
                self.send_response(500); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)
            return
        if parsed.path == "/research/daily/intraday":
            try:
                body = json.dumps(research_daily_intraday_payload(self.pool, parse_qs(parsed.query)), ensure_ascii=False, default=_json_default).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
            except Exception as error:
                logging.exception("daily intraday observation query failed")
                body = json.dumps({"status": "ERROR", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False).encode("utf-8")
                self.send_response(500); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)
            return
        if parsed.path == "/research/performance/cycles":
            body = json.dumps(research_cycle_payload(self.pool, parse_qs(parsed.query)), ensure_ascii=False, default=_json_default).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)
            return
        if parsed.path == "/research/performance":
            self.path = "/research-performance.html"
        elif parsed.path == "/research/daily":
            self.path = "/research-daily.html"
        super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path != "/admin/backfill":
            return self._read_only()
        length = int(self.headers.get("Content-Length", "0"))
        values = parse_qs(self.rfile.read(length).decode("utf-8"))
        try:
            payload, status = run_research_backfill(self.pool, values), 200
        except Exception as error:
            logging.exception("research backfill request failed")
            payload, status = {"error": f"{type(error).__name__}: {error}"}, 400
        body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    do_PUT = _read_only
    do_PATCH = _read_only
    do_DELETE = _read_only


def exporter(pool, output: Path, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            payload = dashboard_payload(pool)
            atomic_write(output, payload)
        except Exception:
            logging.exception("dashboard JSON export failed; collection is unaffected")
        stop.wait(5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if "test" not in os.getenv("DB_NAME", "").lower():
        raise RuntimeError("대시보드는 trading_system_v2_test DB에서만 실행합니다.")
    reports = ROOT / "reports" / "multi-ma"
    output = reports / "data" / "latest.json"
    pool = create_connection_pool(DatabaseSettings.from_environment())
    stop = threading.Event()
    thread = threading.Thread(target=exporter, args=(pool, output, stop), daemon=True)
    thread.start()
    try:
        DashboardHandler.pool = pool
        handler = lambda *a, **k: DashboardHandler(*a, directory=str(reports), **k)
        ThreadingHTTPServer((args.bind, args.port), handler).serve_forever()
    finally:
        stop.set(); pool.close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
