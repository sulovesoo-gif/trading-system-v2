"""국내주식 과거 1분봉 RAW Collector.

한국투자증권 주식일별분봉조회(FHKST03010230) 한 API만 호출한다.
페이지 반복과 저장은 백필 서비스 계층의 책임이다.
"""

from __future__ import annotations

from ..base import BaseCollector
from ..converters import combine_kst_datetime, to_decimal, to_int


class StockHistoricalMinuteCollector(BaseCollector):
    path = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
    tr_id = "FHKST03010230"
    _venue_to_market_division = {"KRX": "J", "NXT": "NX", "INTEGRATED": "UN"}

    def collect(
        self,
        *,
        stock_code: str,
        market_code: str,
        trading_venue: str,
        input_date: str,
        input_hour: str,
        previous_data_include_yn: str = "Y",
        fake_tick_include_yn: str = "N",
        continuation: str | None = None,
        collect_cycle: str = "1MIN",
    ) -> list[dict[str, object]]:
        try:
            market_division = self._venue_to_market_division[trading_venue]
        except KeyError as error:
            raise ValueError("trading_venue must be KRX, NXT, or INTEGRATED.") from error

        payload = self.client.get(
            path=self.path,
            tr_id=self.tr_id,
            params={
                "FID_COND_MRKT_DIV_CODE": market_division,
                "FID_INPUT_ISCD": stock_code,
                "FID_INPUT_DATE_1": input_date,
                "FID_INPUT_HOUR_1": input_hour,
                "FID_PW_DATA_INCU_YN": previous_data_include_yn,
                "FID_FAKE_TICK_INCU_YN": fake_tick_include_yn,
            },
            extra_headers={"tr_cont": continuation} if continuation else None,
        )

        rows: list[dict[str, object]] = []
        for output in self.output_list(payload, "output2"):
            self.require_fields(
                output,
                (
                    "stck_bsop_date",
                    "stck_cntg_hour",
                    "stck_oprc",
                    "stck_hgpr",
                    "stck_lwpr",
                    "stck_prpr",
                    "cntg_vol",
                    "acml_tr_pbmn",
                ),
            )
            row = self.metadata(
                market_code=market_code,
                collect_cycle=collect_cycle,
                trading_venue=trading_venue,
            )
            row.update(
                {
                    "bar_time": combine_kst_datetime(
                        output.get("stck_bsop_date"),
                        output.get("stck_cntg_hour"),
                        collection_time=row["collected_at"],
                    ),
                    "stock_code": stock_code,
                    "open_price": to_decimal(output.get("stck_oprc")),
                    "high_price": to_decimal(output.get("stck_hgpr")),
                    "low_price": to_decimal(output.get("stck_lwpr")),
                    "close_price": to_decimal(output.get("stck_prpr")),
                    "volume": to_int(output.get("cntg_vol")),
                    "accumulated_amount": to_decimal(output.get("acml_tr_pbmn")),
                    "raw_payload": output,
                }
            )
            rows.append(row)
        return rows
