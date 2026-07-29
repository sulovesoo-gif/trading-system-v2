"""RAW Collector 공통 보조 기능."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from .converters import kst_now


class BaseCollector:
    data_source = "KIS"

    def __init__(self, client, *, now_provider: Callable[[], datetime] | None = None) -> None:
        self.client = client
        self._now = now_provider or kst_now

    def metadata(
        self,
        *,
        market_code: str,
        collect_cycle: str,
        trading_venue: str | None = None,
    ) -> dict[str, object]:
        # collected_at은 API 응답을 수신해 RAW 행으로 변환한 실제 KST 수집 시각이다.
        collected_at = self._now()
        metadata: dict[str, object] = {
            "collected_at": collected_at,
            "data_source": self.data_source,
            "market_code": market_code,
            "collect_cycle": collect_cycle,
        }
        if trading_venue is not None:
            metadata["trading_venue"] = trading_venue
        return metadata

    @staticmethod
    def output_dict(payload: dict, key: str = "output") -> dict:
        output = payload.get(key)
        if not isinstance(output, dict):
            raise ValueError(f"KIS 응답의 {key}가 객체가 아닙니다.")
        return output

    @staticmethod
    def output_list(payload: dict, key: str) -> list[dict]:
        output = payload.get(key)
        if not isinstance(output, list) or not all(isinstance(item, dict) for item in output):
            raise ValueError(f"KIS 응답의 {key}가 객체 목록이 아닙니다.")
        return output

    @staticmethod
    def require_fields(output: dict, fields: tuple[str, ...]) -> None:
        missing = [field for field in fields if field not in output]
        if missing:
            raise ValueError(f"KIS 응답에 필수 필드가 없습니다: {', '.join(missing)}")
