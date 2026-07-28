"""국내 주식 체결 시세 Collector."""

from __future__ import annotations

from ..base import BaseCollector
from ..converters import combine_kst_datetime, to_decimal, to_int, to_text


class StockExecutionCollector(BaseCollector):
    path = "/uapi/domestic-stock/v1/quotations/inquire-ccnl"
    tr_id = "FHKST01010300"

    def collect(
        self, *, stock_code: str, market_code: str, collect_cycle: str = "1MIN"
    ) -> list[dict[str, object]]:
        payload = self.client.get(
            path=self.path,
            tr_id=self.tr_id,
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
        )
        rows: list[dict[str, object]] = []
        required = ("stck_cntg_hour", "stck_prpr", "prdy_vrss", "prdy_vrss_sign", "prdy_ctrt", "cntg_vol", "tday_rltv")
        for output in self.output_list(payload, "output"):
            self.require_fields(output, required)
            row = self.metadata(market_code=market_code, collect_cycle=collect_cycle)
            row.update(
                {
                    "snapshot_time": combine_kst_datetime(None, to_text(output.get("stck_cntg_hour")), collection_time=row["collected_at"]),
                    "stock_code": stock_code, "current_price": to_decimal(output.get("stck_prpr")),
                    "previous_day_difference": to_decimal(output.get("prdy_vrss")), "previous_day_difference_sign": to_text(output.get("prdy_vrss_sign")),
                    "change_rate": to_decimal(output.get("prdy_ctrt")), "execution_volume": to_int(output.get("cntg_vol")),
                    "execution_strength": to_decimal(output.get("tday_rltv")), "raw_payload": output,
                }
            )
            rows.append(row)
        return rows
