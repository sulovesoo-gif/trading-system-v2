"""KIS UN 통합 1분봉의 승인된 분석 세션 규칙."""

from __future__ import annotations

from datetime import datetime, time
from typing import Sequence, TypeVar


_PREMARKET_GAP_START = time(8, 50)
_PREMARKET_GAP_END = time(8, 59, 59, 999999)
_Bar = TypeVar("_Bar")


def is_valid_integrated_analysis_time(bar_time: datetime) -> bool:
    """08:50~08:59 KST의 NXT 프리마켓-본장 공백을 분석에서 제외한다.

    이 시간대에는 KIS UN 응답이 거래량 0·반복 OHLC 정지값을 반환할 수 있다.
    거래량 0만으로 다른 시간대의 봉을 일괄 제외하지 않으며, 공백 봉을 만들거나
    보간하지 않는다.
    """
    return not _PREMARKET_GAP_START <= bar_time.time() <= _PREMARKET_GAP_END


def filter_integrated_analysis_bars(bars: Sequence[_Bar]) -> list[_Bar]:
    """bar_time을 가진 봉에서 승인된 UN 세션 공백만 제거한다."""
    return [bar for bar in bars if is_valid_integrated_analysis_time(bar.bar_time)]
