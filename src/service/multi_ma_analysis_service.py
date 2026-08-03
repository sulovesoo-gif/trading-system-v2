"""완료 1분봉과 한 개의 진행봉 스냅샷을 사용한 분석 전용 서비스."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.analysis.event.multi_ma_event import MultiMaSignal, detect_signals
from src.analysis.feature.multi_ma_feature import MultiMaFeature, build_multi_ma_features
from src.analysis.feature.sma_feature import MinuteBar
from src.analysis.strategy.multi_ma_strategy import StrategyState, apply_accumulated, apply_single_signal


STRATEGY_SIGNAL_1 = "SIGNAL_1_ONLY"
STRATEGY_SIGNAL_2 = "SIGNAL_2_ONLY"
STRATEGY_SIGNAL_3 = "SIGNAL_3_ONLY"
STRATEGY_ACCUMULATED = "ACCUMULATED"
STRATEGY_CODES = (STRATEGY_SIGNAL_1, STRATEGY_SIGNAL_2, STRATEGY_SIGNAL_3, STRATEGY_ACCUMULATED)


@dataclass(frozen=True)
class AnalysisResult:
    feature: MultiMaFeature
    signals: tuple[MultiMaSignal, ...]
    actions: dict[str, tuple]


class MultiMaAnalysisService:
    """상태는 호출자가 슬롯별로 분리하여 전달한다. 주문·알림·RAW 수정은 하지 않는다."""

    def analyze(
        self,
        *,
        completed_bars: list[MinuteBar],
        in_progress_bar: MinuteBar | None,
        ma_config,
        states: dict[str, StrategyState],
        previous_feature: MultiMaFeature | None = None,
    ) -> AnalysisResult | None:
        bars = list(completed_bars)
        if ma_config.include_in_progress and in_progress_bar is not None:
            if bars and in_progress_bar.bar_time <= bars[-1].bar_time:
                raise ValueError("진행봉 스냅샷은 마지막 완료봉 이후 시각이어야 합니다.")
            bars.append(in_progress_bar)
        features = build_multi_ma_features(
            bars,
            short_period=ma_config.short_period,
            mid_period=ma_config.mid_period,
            long_period=ma_config.long_period,
            price_field=ma_config.price_field,
        )
        if not features:
            return None
        current = features[-1]
        # Each observation (SEC_05…COMPLETE) must compare with its own
        # preceding observation, never with an in-call completed-bar feature.
        signals = tuple(detect_signals(previous_feature, current))
        grouped = {kind: [item for item in signals if item.signal_type == kind] for kind in ("SIGNAL_1", "SIGNAL_2", "SIGNAL_3")}
        actions = {
            STRATEGY_SIGNAL_1: tuple(apply_single_signal(states[STRATEGY_SIGNAL_1], item, accepted_type="SIGNAL_1") for item in grouped["SIGNAL_1"]),
            STRATEGY_SIGNAL_2: tuple(apply_single_signal(states[STRATEGY_SIGNAL_2], item, accepted_type="SIGNAL_2") for item in grouped["SIGNAL_2"]),
            STRATEGY_SIGNAL_3: tuple(apply_single_signal(states[STRATEGY_SIGNAL_3], item, accepted_type="SIGNAL_3") for item in grouped["SIGNAL_3"]),
            STRATEGY_ACCUMULATED: tuple(apply_accumulated(states[STRATEGY_ACCUMULATED], signals)),
        }
        return AnalysisResult(current, signals, actions)


def new_slot_states() -> dict[str, StrategyState]:
    return {code: StrategyState() for code in STRATEGY_CODES}
