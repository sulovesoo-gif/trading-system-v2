"""공통코드 기반 완료 1분봉·5초 스냅샷·다중 MA 분석 실행기.

주문, 계좌, 기존 SMA 신호 테이블 및 ntfy를 사용하지 않는다.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, time as clock_time, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.analysis.feature.sma_feature import MinuteBar
from src.analysis.feature.multi_ma_feature import MultiMaFeature
from src.collector.raw.converters import kst_now
from src.collector.raw.domestic_stock.stock_minute_collector import StockMinuteCollector
from src.collector.raw.kis_client import KISClient
from src.repository.common_code_repository import CommonCodeRepository
from src.repository.database import DatabaseSettings, create_connection_pool
from src.repository.raw_repository import RawRepository
from src.repository.raw_specs import RawTable
from src.repository.multi_ma_repository import MultiMaRepository, MultiMaStateKey
from src.repository.multi_ma_performance_repository import MultiMaPerformanceKey, MultiMaPerformanceRepository
from src.repository.stock_minute_analysis_repository import StockMinuteAnalysisRepository
from src.service.multi_ma_analysis_service import MultiMaAnalysisService, STRATEGY_CODES, new_slot_states
from src.service.multi_ma_performance_service import MultiMaPerformanceService
from src.service.raw_ingestion_service import RawIngestionService
from src.service.stock_minute_snapshot_service import SCHEDULED_SNAPSHOT_SECONDS, StockMinuteSnapshotService


def _previous_completed(rows, now):
    """Return only the API row whose raw timestamp is the expected prior minute.

    A rolling KIS response can temporarily expose a flat, zero-volume placeholder
    for the just-finished minute.  It is not an official completed bar and must
    never be frozen into ``raw_stock_minute`` by the insert-only RAW policy.
    """
    target = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
    candidates = [row for row in rows if row.get("bar_time") == target]
    if len(candidates) != 1:
        print(f"MISSING_COMPLETED_BAR expected={target.isoformat()} reason=RAW_TIMESTAMP_NOT_FOUND")
        return None
    row = candidates[0]
    open_price, high_price = row.get("open_price"), row.get("high_price")
    low_price, close_price, volume = row.get("low_price"), row.get("close_price"), row.get("volume")
    if None in (open_price, high_price, low_price, close_price, volume) or high_price < max(open_price, close_price) or low_price > min(open_price, close_price) or volume < 0:
        print(f"MISSING_COMPLETED_BAR expected={target.isoformat()} reason=INVALID_OHLCV")
        return None
    if volume == 0 and open_price == high_price == low_price == close_price:
        print(f"MISSING_COMPLETED_BAR expected={target.isoformat()} reason=PRELIMINARY_ZERO_VOLUME_PLACEHOLDER")
        return None
    return row


def _snapshot_bar(row) -> MinuteBar:
    return MinuteBar(row["target_bar_time"], row["open_price"], row["high_price"], row["low_price"], row["close_price"])


def _analysis_session_gap(bar_time: datetime) -> bool:
    """Do not synthesize or analyze the documented 08:50–08:59 venue gap."""
    return clock_time(8, 50) <= bar_time.time() <= clock_time(8, 59, 59)


def _has_unexpected_data_gap(bars, snapshot_bar=None) -> bool:
    series = list(bars) + ([] if snapshot_bar is None else [snapshot_bar])
    for previous, current in zip(series, series[1:]):
        if current.bar_time - previous.bar_time <= timedelta(minutes=1):
            continue
        if previous.bar_time.time() == clock_time(8, 49) and current.bar_time.time() == clock_time(9, 0):
            continue
        return True
    return False


class Runtime:
    def __init__(self, pool, *, restore_feature_state: bool = True) -> None:
        self.pool = pool
        self.codes = CommonCodeRepository(pool)
        self.raw = RawIngestionService(RawRepository(pool))
        self.collector = StockMinuteCollector(KISClient())
        self.minutes = StockMinuteAnalysisRepository(pool)
        self.analysis = MultiMaAnalysisService()
        self.multi_repository = MultiMaRepository(pool)
        self.performance = MultiMaPerformanceService(MultiMaPerformanceRepository(pool))
        self.states: dict[tuple[str, str, str, str, str], dict] = {}
        self.previous_features: dict[tuple[str, str, str, str, str], MultiMaFeature] = {}
        self.restore_feature_state = restore_feature_state

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
        candidate_time = snapshot_bar.bar_time if snapshot_bar is not None else before_time
        if _analysis_session_gap(candidate_time):
            return
        config = self.codes.active_ma_config("MA_3_5_10")
        # One extra completed bar is required to calculate the current
        # short-MA slope.  Signal comparison itself is against the prior
        # feature of this exact observation slot (below).
        bars = self.minutes.completed_bars(stock_code=stock_code, before_time=before_time, limit=config.long_period + 1, trading_venue=venue)
        if _has_unexpected_data_gap(bars, snapshot_bar):
            print(f"DATA_GAP stock={stock_code} venue={venue} slot={slot} before={before_time}")
            return
        state_key = (stock_code, venue, slot, config.code, config.price_field)
        states = self.states.setdefault(state_key, new_slot_states())
        previous = self.previous_features.get(state_key)
        if previous is None and self.restore_feature_state:
            restore_key = MultiMaStateKey(stock_code, "KOSPI", venue, STRATEGY_CODES[0], slot, config.code, config.price_field)
            restored = self.multi_repository.load_feature_state(restore_key)
            if restored is not None:
                processed_at, ma_short, ma_mid, ma_long, short_slope = restored
                previous = MultiMaFeature(
                    MinuteBar(processed_at, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")),
                    Decimal("0"), Decimal(ma_short), Decimal(ma_mid), Decimal(ma_long),
                    None if short_slope is None else Decimal(short_slope),
                )
        result = self.analysis.analyze(
            completed_bars=bars, in_progress_bar=snapshot_bar, ma_config=config,
            states=states, previous_feature=previous,
        )
        if result is None:
            return
        self.previous_features[state_key] = result.feature
        accepted = {
            "SIGNAL_1_ONLY": {"SIGNAL_1"}, "SIGNAL_2_ONLY": {"SIGNAL_2"},
            "SIGNAL_3_ONLY": {"SIGNAL_3"}, "ACCUMULATED": {"SIGNAL_1", "SIGNAL_2", "SIGNAL_3"},
        }
        accumulated_conflict = len({signal.direction for signal in result.signals}) > 1
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
                    # ACCUMULATED may receive multiple signal types at one
                    # observation.  Mixed LONG/SHORT directions are a data
                    # conflict, not a deterministic reversal; keep its
                    # existing position unchanged and leave an audit log.
                    if strategy_code == "ACCUMULATED" and accumulated_conflict:
                        continue
                    performance_strategy = strategy_code.replace("_ONLY", "")
                    performance_key = MultiMaPerformanceKey(
                        result.feature.bar.bar_time.date(), stock_code, venue, performance_strategy,
                        "COMPLETE" if slot == "COMPLETE" else f"SEC_{slot}", config.code, config.price_field,
                    )
                    self.performance.process_signal(
                        performance_key, signal_no=signal.signal_type, direction=signal.direction,
                        signal_time=result.feature.bar.bar_time, price=result.feature.value, reason=signal.reason,
                    )
        if result.signals:
            print(f"다중 MA 신호 stock={stock_code} venue={venue} slot={slot} time={result.feature.bar.bar_time} signals={[signal.signal_type + ':' + signal.direction for signal in result.signals]}")
        if accumulated_conflict:
            print(f"다중 MA ACCUMULATED conflict stock={stock_code} venue={venue} slot={slot} time={result.feature.bar.bar_time}")


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
