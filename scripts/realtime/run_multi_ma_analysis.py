"""공통코드 기반 완료 1분봉·5초 스냅샷·다중 MA 분석 실행기.

주문, 계좌, 기존 SMA 신호 테이블 및 ntfy를 사용하지 않는다.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.analysis.feature.sma_feature import MinuteBar
from src.collector.raw.converters import kst_now
from src.collector.raw.domestic_stock.stock_minute_collector import StockMinuteCollector
from src.collector.raw.kis_client import KISClient
from src.repository.common_code_repository import CommonCodeRepository
from src.repository.database import DatabaseSettings, create_connection_pool
from src.repository.raw_repository import RawRepository
from src.repository.raw_specs import RawTable
from src.repository.multi_ma_repository import MultiMaRepository, MultiMaStateKey
from src.repository.stock_minute_analysis_repository import StockMinuteAnalysisRepository
from src.service.multi_ma_analysis_service import MultiMaAnalysisService, STRATEGY_CODES, new_slot_states
from src.service.raw_ingestion_service import RawIngestionService
from src.service.stock_minute_snapshot_service import SCHEDULED_SNAPSHOT_SECONDS, StockMinuteSnapshotService


def _previous_completed(rows, now):
    target = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
    candidates = [row for row in rows if row.get("bar_time") == target]
    return candidates[0] if len(candidates) == 1 else None


def _snapshot_bar(row) -> MinuteBar:
    return MinuteBar(row["target_bar_time"], row["open_price"], row["high_price"], row["low_price"], row["close_price"])


class Runtime:
    def __init__(self, pool) -> None:
        self.pool = pool
        self.codes = CommonCodeRepository(pool)
        self.raw = RawIngestionService(RawRepository(pool))
        self.collector = StockMinuteCollector(KISClient())
        self.minutes = StockMinuteAnalysisRepository(pool)
        self.analysis = MultiMaAnalysisService()
        self.multi_repository = MultiMaRepository(pool)
        self.states: dict[tuple[str, str], dict] = {}

    def cycle(self, now: datetime) -> None:
        if not self.codes.switch_enabled("GLOBAL_COLLECT_YN"):
            return
        for stock in self.codes.enabled_minute_stocks():
            venue = stock.default_market_code
            rows = self.collector.collect(stock_code=stock.stock_code, market_code="KOSPI", trading_venue=venue, input_hour=now.strftime("%H%M%S"))
            if now.second == 1:
                completed = _previous_completed(rows, now)
                if completed is not None:
                    self.raw.store(RawTable.STOCK_MINUTE, completed)
                    self._analyze(stock.stock_code, venue, "COMPLETE", completed["bar_time"], None)
            elif now.second in SCHEDULED_SNAPSHOT_SECONDS:
                snapshot = StockMinuteSnapshotService.build_snapshot(collector_rows=rows, observed_at=now)
                if snapshot is not None:
                    self.raw.store(RawTable.STOCK_MINUTE_SNAPSHOT, snapshot)
                    if now.second != 0:
                        self._analyze(stock.stock_code, venue, f"{now.second:02d}", snapshot["target_bar_time"], _snapshot_bar(snapshot))

    def _analyze(self, stock_code: str, venue: str, slot: str, before_time, snapshot_bar) -> None:
        if not self.codes.switch_enabled("GLOBAL_ANALYSIS_YN"):
            return
        config = self.codes.active_ma_config("MA_3_5_10")
        bars = self.minutes.completed_bars(stock_code=stock_code, before_time=before_time, limit=config.long_period, trading_venue=venue)
        state_key = (stock_code, slot)
        states = self.states.setdefault(state_key, new_slot_states())
        result = self.analysis.analyze(completed_bars=bars, in_progress_bar=snapshot_bar, ma_config=config, states=states)
        if result is None:
            return
        accepted = {
            "SIGNAL_1_ONLY": {"SIGNAL_1"}, "SIGNAL_2_ONLY": {"SIGNAL_2"},
            "SIGNAL_3_ONLY": {"SIGNAL_3"}, "ACCUMULATED": {"SIGNAL_1", "SIGNAL_2", "SIGNAL_3"},
        }
        for strategy_code in STRATEGY_CODES:
            key = MultiMaStateKey(stock_code, "KOSPI", venue, strategy_code, slot, config.code, config.price_field)
            state = states[strategy_code]
            self.multi_repository.upsert_state(
                key, last_processed_time=result.feature.bar.bar_time, ma_short=result.feature.ma_short,
                ma_mid=result.feature.ma_mid, ma_long=result.feature.ma_long, short_slope=result.feature.short_slope,
                previous_short_slope=None, direction=state.direction, weight=state.weight, applied_signals=state.applied_signals,
            )
            for signal in result.signals:
                if signal.signal_type in accepted[strategy_code]:
                    self.multi_repository.record_signal(key, signal=signal, feature=result.feature)
        if result.signals:
            print(f"다중 MA 신호 stock={stock_code} venue={venue} slot={slot} time={result.feature.bar.bar_time} signals={[signal.signal_type + ':' + signal.direction for signal in result.signals]}")


def next_tick(now: datetime) -> datetime:
    minute = now.replace(microsecond=0)
    candidates = [minute.replace(second=1)] + [minute.replace(second=second) for second in SCHEDULED_SNAPSHOT_SECONDS]
    future = [candidate for candidate in candidates if candidate > now]
    return min(future) if future else (minute + timedelta(minutes=1)).replace(second=0)


def is_observation_time(now: datetime) -> bool:
    """초기 MARKET seed의 공통 관찰 구간. 휴장일 API 연결 전에는 평일만 허용한다."""
    return now.weekday() < 5 and clock_time(8, 0) <= now.time() <= clock_time(20, 5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="현재 시각의 조건에 맞는 한 주기만 수행")
    parser.add_argument("--allow-non-test-db", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if "test" not in os.getenv("DB_NAME", "").lower() and not args.allow_non_test_db:
        raise RuntimeError("기본 실행은 test가 포함된 테스트 DB만 허용합니다.")
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        runtime = Runtime(pool)
        while True:
            now = kst_now()
            if not is_observation_time(now):
                time.sleep(1)
                continue
            if now.second in SCHEDULED_SNAPSHOT_SECONDS or now.second == 1:
                runtime.cycle(now)
                if args.once:
                    return 0
            if args.once:
                return 0
            time.sleep(max(0.05, (next_tick(now) - now).total_seconds()))
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
