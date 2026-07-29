"""한국투자증권 국내휴장일조회 Collector.

백필 서비스의 거래일 판정에만 사용한다. 이 API 응답은 가격 RAW 행이 아니므로
별도 RAW 테이블에 저장하지 않으며, 계산이나 전략 판단도 수행하지 않는다.
"""

from __future__ import annotations

from datetime import date

from ..base import BaseCollector
from ..converters import parse_yyyymmdd, to_text


class HolidayCalendarCollector(BaseCollector):
    path = "/uapi/domestic-stock/v1/quotations/chk-holiday"
    tr_id = "CTCA0903R"

    def collect(self, *, base_date: date) -> list[dict[str, object]]:
        payload = self.client.get(
            path=self.path,
            tr_id=self.tr_id,
            params={
                "BASS_DT": base_date.strftime("%Y%m%d"),
                "CTX_AREA_NK": "",
                "CTX_AREA_FK": "",
            },
        )
        rows: list[dict[str, object]] = []
        for output in self.output_list(payload, "output"):
            self.require_fields(output, ("bass_dt", "opnd_yn"))
            rows.append(
                {
                    "trade_date": parse_yyyymmdd(output["bass_dt"]),
                    "open_yn": to_text(output.get("opnd_yn")),
                    "raw_payload": output,
                }
            )
        return rows
