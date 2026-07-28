"""국내 선물옵션 분봉 Collector."""

from __future__ import annotations

from ..base import BaseCollector
from ..converters import combine_kst_datetime, to_decimal, to_int


class FuturesMinuteCollector(BaseCollector):
    path = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
    tr_id = "FHKIF03020200"

    def collect(
        self,
        *,
        futures_code: str,
        market_code: str,
        input_date: str,
        input_hour: str,
        hour_classification_code: str = "0",
        previous_data_include_yn: str = "Y",
        fake_tick_include_yn: str = "N",
        collect_cycle: str = "1MIN",
    ) -> list[dict[str, object]]:
        payload = self.client.get(
            path=self.path,
            tr_id=self.tr_id,
            params={
                "FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": futures_code,
                "FID_HOUR_CLS_CODE": hour_classification_code,
                "FID_PW_DATA_INCU_YN": previous_data_include_yn,
                "FID_FAKE_TICK_INCU_YN": fake_tick_include_yn,
                "FID_INPUT_DATE_1": input_date, "FID_INPUT_HOUR_1": input_hour,
            },
        )
        rows: list[dict[str, object]] = []
        for output in self.output_list(payload, "output2"):
            self.require_fields(output, ("stck_bsop_date", "stck_cntg_hour", "futs_oprc", "futs_hgpr", "futs_lwpr", "futs_prpr", "cntg_vol", "acml_tr_pbmn"))
            row = self.metadata(market_code=market_code, collect_cycle=collect_cycle)
            row.update(
                {
                    "bar_time": combine_kst_datetime(
                        output.get("stck_bsop_date"), output.get("stck_cntg_hour"),
                        collection_time=row["collected_at"],
                    ),
                    "futures_code": futures_code,
                    "open_price": to_decimal(output.get("futs_oprc")),
                    "high_price": to_decimal(output.get("futs_hgpr")),
                    "low_price": to_decimal(output.get("futs_lwpr")),
                    "close_price": to_decimal(output.get("futs_prpr")),
                    "volume": to_int(output.get("cntg_vol")),
                    "accumulated_amount": to_decimal(output.get("acml_tr_pbmn")),
                    "raw_payload": output,
                }
            )
            rows.append(row)
        return rows
