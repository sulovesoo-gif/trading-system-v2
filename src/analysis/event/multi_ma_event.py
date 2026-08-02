"""다중 SMA의 세 가지 승인된 타점 정의."""

from __future__ import annotations

from dataclasses import dataclass

from src.analysis.feature.multi_ma_feature import MultiMaFeature


@dataclass(frozen=True)
class MultiMaSignal:
    signal_type: str
    direction: str
    reason: str


def detect_signals(previous: MultiMaFeature | None, current: MultiMaFeature) -> list[MultiMaSignal]:
    if previous is None or previous.short_slope is None or current.short_slope is None:
        return []
    signals: list[MultiMaSignal] = []
    if previous.short_slope <= 0 < current.short_slope:
        signals.append(MultiMaSignal("SIGNAL_1", "LONG", "단기 이동평균 기울기 상향 전환"))
    if previous.short_slope >= 0 > current.short_slope:
        signals.append(MultiMaSignal("SIGNAL_1", "SHORT", "단기 이동평균 기울기 하향 전환"))
    if previous.ma_short <= previous.ma_mid and current.ma_short > current.ma_mid:
        signals.append(MultiMaSignal("SIGNAL_2", "LONG", "단기·중기 이동평균 상향 교차"))
    if previous.ma_short >= previous.ma_mid and current.ma_short < current.ma_mid:
        signals.append(MultiMaSignal("SIGNAL_2", "SHORT", "단기·중기 이동평균 하향 교차"))
    if not _aligned_long(previous) and _aligned_long(current):
        signals.append(MultiMaSignal("SIGNAL_3", "LONG", "단기·중기·장기 정배열 진입"))
    if not _aligned_short(previous) and _aligned_short(current):
        signals.append(MultiMaSignal("SIGNAL_3", "SHORT", "단기·중기·장기 역배열 진입"))
    return signals


def _aligned_long(feature: MultiMaFeature) -> bool:
    return feature.ma_short > feature.ma_mid > feature.ma_long


def _aligned_short(feature: MultiMaFeature) -> bool:
    return feature.ma_short < feature.ma_mid < feature.ma_long
