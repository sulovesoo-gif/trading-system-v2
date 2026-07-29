"""완료 1분봉 종가 기반 단순이동평균 Feature."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence


@dataclass(frozen=True)
class MinuteBar:
    bar_time: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal


@dataclass(frozen=True)
class SmaFeature:
    bar: MinuteBar
    sma5: Decimal
    sma10: Decimal


def build_sma_features(bars: Sequence[MinuteBar]) -> list[SmaFeature]:
    """시간 오름차순의 완료 봉에서 SMA5·SMA10을 계산한다.

    Feature는 호출 중에만 존재하며 RAW 테이블에는 저장하지 않는다.
    """
    ordered = list(bars)
    if any(left.bar_time >= right.bar_time for left, right in zip(ordered, ordered[1:])):
        raise ValueError("완료 1분봉은 bar_time 오름차순이며 중복이 없어야 합니다.")
    features: list[SmaFeature] = []
    for index in range(9, len(ordered)):
        closes = [item.close_price for item in ordered]
        features.append(SmaFeature(
            bar=ordered[index],
            sma5=sum(closes[index - 4:index + 1]) / Decimal("5"),
            sma10=sum(closes[index - 9:index + 1]) / Decimal("10"),
        ))
    return features
