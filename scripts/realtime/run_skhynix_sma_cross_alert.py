"""통합 KIS 분봉 디스패처: 기존 SMA 알림과 다중 MA 분석을 한 호출 결과로 처리한다."""
from __future__ import annotations
import argparse, os, sys, time
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from src.collector.raw.converters import kst_now
from src.collector.raw.domestic_stock.stock_minute_collector import StockMinuteCollector
from src.collector.raw.domestic_stock.program_collector import ProgramCollector
from src.collector.raw.kis_client import KISClient
from src.repository.common_code_repository import CommonCodeRepository
from src.repository.database import DatabaseSettings, create_connection_pool
from src.repository.raw_repository import RawRepository
from src.repository.raw_specs import RawTable
from src.repository.sma_cross_signal_repository import SmaCrossSignalRepository
from src.repository.stock_minute_analysis_repository import StockMinuteAnalysisRepository
from src.service.email_alert_service import EmailAlertService, EmailSettings
from src.service.ntfy_alert_service import NtfyAlertService, NtfySettings
from src.service.raw_ingestion_service import RawIngestionService
from src.service.sma_cross_signal_service import SmaCrossSignalService
from src.service.stock_minute_snapshot_service import SCHEDULED_SNAPSHOT_SECONDS, StockMinuteSnapshotService
from scripts.realtime.run_multi_ma_analysis import Runtime as MultiMaRuntime, _snapshot_bar

def active(now: datetime) -> bool:
    return now.weekday() < 5 and clock_time(8, 1) <= now.time() <= clock_time(20, 4)

is_observation_time = active

def completed(rows, now):
    target = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
    found = [row for row in rows if row.get("bar_time") == target]
    return found[0] if len(found) == 1 else None

def immediately_previous_completed_row(rows, *, now): return completed(rows, now)
def completed_rows_for_storage(rows, *, now):
    cutoff = now.replace(second=0, microsecond=0); return [row for row in rows if row.get("bar_time") < cutoff]
def new_completed_bar_times(rows, *, now, last_processed):
    return sorted({row["bar_time"] for row in completed_rows_for_storage(rows, now=now) if last_processed is None or row["bar_time"] > last_processed})
def initial_analysis_watermark(now): return now.replace(second=0, microsecond=0)
def next_minute_one_second(now):
    candidate = now.replace(second=1, microsecond=0); return candidate if candidate > now else candidate + timedelta(minutes=1)

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--interval-seconds", type=int, default=5); parser.add_argument("--dry-run", action="store_true"); parser.add_argument("--allow-non-test-db", action="store_true"); args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if "test" not in os.getenv("DB_NAME", "").lower() and not args.allow_non_test_db: raise RuntimeError("테스트 DB만 허용합니다.")
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        alerts = None if args.dry_run else (NtfyAlertService(NtfySettings.from_environment()) if os.getenv("ALERT_NTFY_ENABLED", "false").lower() == "true" else EmailAlertService(EmailSettings.from_environment()))
        sma = SmaCrossSignalService(minute_repository=StockMinuteAnalysisRepository(pool), signal_repository=SmaCrossSignalRepository(pool), email_service=alerts)
        raw, codes, multi, collector = RawIngestionService(RawRepository(pool)), CommonCodeRepository(pool), MultiMaRuntime(pool), StockMinuteCollector(KISClient())
        program_collector = ProgramCollector(KISClient())
        startup = kst_now(); last_sma = startup.replace(second=0, microsecond=0) - timedelta(minutes=1); last_dispatch = None; sma.restore_armed_state(stock_code="000660", before_time=startup)
        while True:
            now = kst_now()
            tick = now.replace(microsecond=0)
            schedule = codes.api_schedule("STOCK_PROGRAM_1MIN")
            program_due = schedule.due(now)
            if not active(now) or now.second not in (*SCHEDULED_SNAPSHOT_SECONDS, 1, schedule.execution_second if program_due else -1) or tick == last_dispatch: time.sleep(.2); continue
            last_dispatch = tick
            for stock in codes.enabled_minute_stocks():
                venue = stock.default_market_code
                if program_due and stock.analysis_yn and stock.program_collect_yn:
                    try:
                        raw.store(RawTable.PROGRAM, program_collector.collect(stock_code=stock.stock_code, market_code="KOSPI", trading_venue=venue))
                    except Exception as error:
                        print(f"KIS program collection failed: {type(error).__name__}")
                    continue
                if now.second not in (*SCHEDULED_SNAPSHOT_SECONDS, 1):
                    continue
                try:
                    rows = collector.collect(stock_code=stock.stock_code, market_code="KOSPI", trading_venue=venue, input_hour=now.strftime("%H%M%S"))
                except Exception as error:
                    # 인증·네트워크 오류가 수집 서비스 전체 종료로 위장되지 않게 한다.
                    print(f"KIS minute collection failed: {type(error).__name__}")
                    continue
                if now.second == 1:
                    row = completed(rows, now)
                    if row is None: continue
                    raw.store(RawTable.STOCK_MINUTE, row); multi._analyze(stock.stock_code, venue, "COMPLETE", row["bar_time"], None)
                    if stock.stock_code == "000660" and venue == "INTEGRATED" and row["bar_time"] > last_sma:
                        sma.evaluate_completed_bar(stock_code="000660", completed_time=row["bar_time"])
                        latest = StockMinuteAnalysisRepository(pool).completed_bars(stock_code="000660", before_time=row["bar_time"], limit=1)
                        if latest: sma.update_open_performance(stock_code="000660", completed_bar=latest[-1])
                        if row["bar_time"].hour == 15 and row["bar_time"].minute == 30:
                            sma.close_market_performance(stock_code="000660", market_close_bar_time=row["bar_time"])
                            multi.performance.close_trade_date(row["bar_time"].date(), exit_time=row["bar_time"], exit_price=row["close_price"])
                        last_sma = row["bar_time"]
                else:
                    snapshot = StockMinuteSnapshotService.build_snapshot(collector_rows=rows, observed_at=now)
                    if snapshot is not None:
                        raw.store(RawTable.STOCK_MINUTE_SNAPSHOT, snapshot)
                        if now.second != 0: multi._analyze(stock.stock_code, venue, f"{now.second:02d}", snapshot["target_bar_time"], _snapshot_bar(snapshot))
            time.sleep(.2)
    finally: pool.close()
if __name__ == "__main__": raise SystemExit(main())
