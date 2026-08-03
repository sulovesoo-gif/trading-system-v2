"""읽기 전용 다중 MA 대시보드: JSON은 원자적으로 갱신하고 HTTP는 localhost에만 바인딩한다."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
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
        cur.execute("""SELECT bar_time,close_price FROM raw_stock_minute WHERE stock_code='000660'
        AND trading_venue='INTEGRATED' AND collect_cycle='1MIN' AND bar_time::date=CURRENT_DATE ORDER BY bar_time""")
        completed_series = cur.fetchall()
        cur.execute("""SELECT snapshot_time,target_bar_time,close_price FROM raw_stock_minute_snapshot WHERE stock_code='000660'
        AND trading_venue='INTEGRATED' AND collect_cycle='5SEC' AND snapshot_time::date=CURRENT_DATE ORDER BY snapshot_time""")
        snapshot_series = cur.fetchall()
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
        cur.execute("""SELECT signal_time,signal_no,direction,signal_price,observation_code,strategy_code,reason
        FROM analysis_multi_ma_signal WHERE stock_code='000660' AND trading_venue='INTEGRATED' AND trade_date=CURRENT_DATE
        ORDER BY signal_time DESC LIMIT 100""")
        signals = cur.fetchall()
        cur.execute("""SELECT trade_id,cycle_no,entry_time,direction,entry_price,entry_ratio,average_entry_price,
        exit_time,exit_price,exit_type,exit_reason,realized_profit_amount,realized_profit_rate,
        strategy_code,observation_code,status
        FROM analysis_multi_ma_trade WHERE stock_code='000660' AND trading_venue='INTEGRATED' AND trade_date=CURRENT_DATE
        ORDER BY entry_time DESC LIMIT 100""")
        trades = cur.fetchall()
        cur.execute("""SELECT leg.trade_id,leg.signal_no,leg.signal_time,leg.entry_price,leg.entry_ratio,
        leg.notional_amount FROM analysis_multi_ma_trade_leg leg
        JOIN analysis_multi_ma_trade trade ON trade.trade_id=leg.trade_id
        WHERE trade.stock_code='000660' AND trade.trading_venue='INTEGRATED' AND trade.trade_date=CURRENT_DATE
        ORDER BY leg.trade_id,leg.signal_time""")
        legs = cur.fetchall()
    columns = lambda names, rows: [dict(zip(names, row)) for row in rows]
    now = datetime.now(KST)
    completed_values = [(row[0], float(row[1])) for row in completed_series]
    def point(timestamp, price):
        values = [value for at, value in completed_values if at < timestamp] + [float(price)]
        average = lambda period: None if len(values) < period else round(sum(values[-period:]) / period, 2)
        return {"timestamp": timestamp, "price": price, "ma_short": average(3), "ma_mid": average(5), "ma_long": average(10)}
    series = {"COMPLETE": [point(at, value) for at, value in completed_values]}
    for second in range(5, 60, 5):
        code = f"SEC_{second:02d}"
        series[code] = [point(row[0], row[2]) for row in snapshot_series if row[0].second == second]
    in_market = now.weekday() < 5 and now.time().strftime("%H:%M") >= "08:00" and now.time().strftime("%H:%M") <= "20:05"
    status = "DATA_MISSING" if in_market and (completed is None or snapshot is None) else ("OPEN" if in_market else "CLOSED")
    return {
        "generated_at": now,
        "market_status": status,
        "strategy_alert_enabled": False,
        "order_enabled": False,
        "latest_completed": None if completed is None else dict(zip(("bar_time","stock_code","trading_venue","close_price"), completed)),
        "latest_snapshot": None if snapshot is None else dict(zip(("snapshot_time","target_bar_time","stock_code","trading_venue","close_price"), snapshot)),
        "completed_count_today": len(completed_series), "snapshot_count_today": len(snapshot_series), "series": series,
        "states": columns(("strategy_code","observation_code","position_direction","position_weight","last_processed_time"), states),
        "summaries": columns(("strategy_code","observation_code","total_profit_amount","total_profit_rate","trade_count","win_count","loss_count","win_rate","signal_exit_count","session_close_exit_count","signal_exit_profit","session_close_exit_profit","max_profit","max_loss"), summaries),
        "signals": columns(("signal_time","signal_no","direction","signal_price","observation_code","strategy_code","reason"), signals),
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
