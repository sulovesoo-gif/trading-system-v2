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
        WHERE stock_code='000660' AND trading_venue='INTEGRATED' AND collect_cycle='1MIN'
        ORDER BY snapshot_time DESC LIMIT 1""")
        snapshot = cur.fetchone()
        cur.execute("""SELECT strategy_code,observation_code,position_direction,position_weight,last_processed_time
        FROM analysis_multi_ma_state WHERE stock_code='000660' AND trading_venue='INTEGRATED'
        ORDER BY strategy_code,observation_code""")
        states = cur.fetchall()
        cur.execute("""SELECT strategy_code,observation_code,total_profit_amount,total_profit_rate,trade_count,
        signal_exit_profit,session_close_exit_profit FROM analysis_multi_ma_summary
        WHERE stock_code='000660' AND trading_venue='INTEGRATED' ORDER BY total_profit_rate DESC""")
        summaries = cur.fetchall()
        cur.execute("""SELECT signal_time,signal_no,direction,signal_price,observation_code,strategy_code,reason
        FROM analysis_multi_ma_signal WHERE stock_code='000660' AND trading_venue='INTEGRATED'
        ORDER BY signal_time DESC LIMIT 100""")
        signals = cur.fetchall()
        cur.execute("""SELECT entry_time,direction,entry_price,entry_ratio,exit_time,exit_price,exit_type,exit_reason,
        realized_profit_amount,realized_profit_rate,strategy_code,observation_code,status
        FROM analysis_multi_ma_trade WHERE stock_code='000660' AND trading_venue='INTEGRATED'
        ORDER BY entry_time DESC LIMIT 100""")
        trades = cur.fetchall()
    columns = lambda names, rows: [dict(zip(names, row)) for row in rows]
    now = datetime.now(KST)
    return {
        "generated_at": now,
        "market_status": "CLOSED" if now.weekday() >= 5 else "OPEN_OR_IDLE",
        "strategy_alert_enabled": False,
        "order_enabled": False,
        "latest_completed": None if completed is None else dict(zip(("bar_time","stock_code","trading_venue","close_price"), completed)),
        "latest_snapshot": None if snapshot is None else dict(zip(("snapshot_time","target_bar_time","stock_code","trading_venue","close_price"), snapshot)),
        "states": columns(("strategy_code","observation_code","position_direction","position_weight","last_processed_time"), states),
        "summaries": columns(("strategy_code","observation_code","total_profit_amount","total_profit_rate","trade_count","signal_exit_profit","session_close_exit_profit"), summaries),
        "signals": columns(("signal_time","signal_no","direction","signal_price","observation_code","strategy_code","reason"), signals),
        "trades": columns(("entry_time","direction","entry_price","entry_ratio","exit_time","exit_price","exit_type","exit_reason","realized_profit_amount","realized_profit_rate","strategy_code","observation_code","status"), trades),
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
    parser.add_argument("--bind", default="127.0.0.1")
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
