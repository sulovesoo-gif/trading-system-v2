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
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from src.repository.database import DatabaseSettings, create_connection_pool

KST = ZoneInfo("Asia/Seoul")


def _json_default(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


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


class DashboardHandler(SimpleHTTPRequestHandler):
    def list_directory(self, path):
        self.send_error(403, "Directory listing disabled")
        return None
    def log_message(self, format, *args):
        logging.info("http %s", format % args)
    def _read_only(self):
        self.send_error(405, "Read-only dashboard")
    do_POST = _read_only
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
        handler = lambda *a, **k: DashboardHandler(*a, directory=str(reports), **k)
        ThreadingHTTPServer((args.bind, args.port), handler).serve_forever()
    finally:
        stop.set(); pool.close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
