"""1분 가격 계열의 설정 기반 다중 SMA Feature."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from src.analysis.feature.sma_feature import MinuteBar


@dataclass(frozen=True)
class MultiMaFeature:
    bar: MinuteBar
    value: Decimal
    ma_short: Decimal
    ma_mid: Decimal
    ma_long: Decimal
    short_slope: Decimal | None


def price_value(bar: MinuteBar, field: str) -> Decimal:
    values = {
        "OPEN": bar.open_price, "HIGH": bar.high_price, "LOW": bar.low_price,
        "CLOSE": bar.close_price, "CURRENT_PRICE": bar.close_price,
        "HL2": (bar.high_price + bar.low_price) / Decimal("2"),
        "HLC3": (bar.high_price + bar.low_price + bar.close_price) / Decimal("3"),
        "OHLC4": (bar.open_price + bar.high_price + bar.low_price + bar.close_price) / Decimal("4"),
    }
    try:
        return values[field]
    except KeyError as error:
        raise ValueError(f"허용되지 않은 PRICE_FIELD: {field}") from error


def build_multi_ma_features(bars: Sequence[MinuteBar], *, short_period: int, mid_period: int, long_period: int, price_field: str) -> list[MultiMaFeature]:
    if not (0 < short_period < mid_period < long_period):
        raise ValueError("MA 기간은 단기 < 중기 < 장기여야 합니다.")
    if any(left.bar_time >= right.bar_time for left, right in zip(bars, bars[1:])):
        raise ValueError("입력 1분봉은 시간 오름차순이며 중복이 없어야 합니다.")
    values = [price_value(bar, price_field) for bar in bars]
    result: list[MultiMaFeature] = []
    for index in range(long_period - 1, len(bars)):
        short = sum(values[index - short_period + 1:index + 1]) / Decimal(short_period)
        mid = sum(values[index - mid_period + 1:index + 1]) / Decimal(mid_period)
        long = sum(values[index - long_period + 1:index + 1]) / Decimal(long_period)
        prior = result[-1].ma_short if result else None
        result.append(MultiMaFeature(bars[index], values[index], short, mid, long, None if prior is None else short - prior))
    return result
