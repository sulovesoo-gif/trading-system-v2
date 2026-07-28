"""국내 주식 당일 분봉 Collector."""

from __future__ import annotations

from ..base import BaseCollector
from ..converters import combine_kst_datetime, to_decimal, to_int


class StockMinuteCollector(BaseCollector):
    path = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
    tr_id = "FHKST03010200"

    def collect(
        self,
        *,
        stock_code: str,
        market_code: str,
        input_hour: str,
        previous_data_include_yn: str = "Y",
        etc_classification_code: str = "",
        collect_cycle: str = "1MIN",
    ) -> list[dict[str, object]]:
        payload = self.client.get(
            path=self.path,
            tr_id=self.tr_id,
            params={
                "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code,
                "FID_INPUT_HOUR_1": input_hour,
                "FID_PW_DATA_INCU_YN": previous_data_include_yn,
                "FID_ETC_CLS_CODE": etc_classification_code,
            },
        )
        rows: list[dict[str, object]] = []
        for output in self.output_list(payload, "output2"):
            self.require_fields(output, ("stck_bsop_date", "stck_cntg_hour", "stck_oprc", "stck_hgpr", "stck_lwpr", "stck_prpr", "cntg_vol", "acml_tr_pbmn"))
            row = self.metadata(market_code=market_code, collect_cycle=collect_cycle)
            row.update(
                {
                    "bar_time": combine_kst_datetime(
                        output.get("stck_bsop_date"), output.get("stck_cntg_hour"),
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
