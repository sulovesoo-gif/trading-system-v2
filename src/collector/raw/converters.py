"""RAW 저장 직전의 최소 타입 변환과 KST 시간 처리."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def kst_now() -> datetime:
    """KST 기준 naive datetime을 반환한다. DB TIMESTAMP 저장용이다."""
    return datetime.now(KST).replace(tzinfo=None)


def as_kst_naive(value: datetime) -> datetime:
    return (value.replace(tzinfo=KST) if value.tzinfo is None else value.astimezone(KST)).replace(tzinfo=None)


def to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    normalized = str(value).replace(",", "").strip()
    return Decimal(normalized) if normalized else None


def to_int(value: Any) -> int | None:
    decimal = to_decimal(value)
    if decimal is None:
        return None
    if decimal != decimal.to_integral_value():
        raise ValueError(f"정수 컬럼에 소수값을 저장할 수 없습니다: {value}")
    return int(decimal)


def to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_yyyymmdd(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def combine_kst_datetime(
    date_value: str | None, time_value: str | None, *, collection_time: datetime | None = None
) -> datetime:
    """API 날짜·시각을 KST naive datetime으로 결합한다.

    날짜 없는 API는 수집일(KST)을 사용한다. 장 마감 후 호출 및 자정 경계의
    정확한 영업일 보정은 실제 응답 검증 후 별도 보완해야 한다.
    """
    base = as_kst_naive(collection_time) if collection_time else kst_now()
    parsed_date = parse_yyyymmdd(date_value) if date_value else base.date()
    raw_time = (time_value or "000000").strip().zfill(6)
    if not raw_time.isdigit():
        raise ValueError(f"KIS 시간 형식이 올바르지 않습니다: {time_value}")
    hours, minutes, seconds = int(raw_time[:2]), int(raw_time[2:4]), int(raw_time[4:6])
    return datetime.combine(parsed_date, time()) + timedelta(
        hours=hours, minutes=minutes, seconds=seconds
    )
