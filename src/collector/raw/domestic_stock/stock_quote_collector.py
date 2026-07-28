"""국내 주식 현재가 시세 Collector."""

from __future__ import annotations

from ..base import BaseCollector
from ..converters import to_decimal, to_int, to_text


class StockQuoteCollector(BaseCollector):
    path = "/uapi/domestic-stock/v1/quotations/inquire-price"
    tr_id = "FHKST01010100"

    def collect(
        self, *, stock_code: str, market_code: str, collect_cycle: str = "1MIN"
    ) -> dict[str, object]:
        payload = self.client.get(
            path=self.path,
            tr_id=self.tr_id,
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
        )
        output = self.output_dict(payload)
        self.require_fields(output, ("stck_prpr", "prdy_vrss", "prdy_vrss_sign", "prdy_ctrt", "stck_oprc", "stck_hgpr", "stck_lwpr", "stck_sdpr", "stck_mxpr", "stck_llam", "acml_vol", "acml_tr_pbmn", "wghn_avrg_stck_prc", "frgn_ntby_qty", "pgtr_ntby_qty", "vi_cls_code", "temp_stop_yn"))
        row = self.metadata(market_code=market_code, collect_cycle=collect_cycle)
        # API가 데이터 기준 시각을 제공하지 않으므로 KST 실제 조회 시각을 snapshot_time으로 사용한다.
        row.update(
            {
                "snapshot_time": row["collected_at"], "stock_code": stock_code,
                "current_price": to_decimal(output.get("stck_prpr")),
                "previous_day_difference": to_decimal(output.get("prdy_vrss")),
                "previous_day_difference_sign": to_text(output.get("prdy_vrss_sign")),
                "change_rate": to_decimal(output.get("prdy_ctrt")),
                "open_price": to_decimal(output.get("stck_oprc")),
                "high_price": to_decimal(output.get("stck_hgpr")),
                "low_price": to_decimal(output.get("stck_lwpr")),
                "base_price": to_decimal(output.get("stck_sdpr")),
                "upper_limit_price": to_decimal(output.get("stck_mxpr")),
                "lower_limit_price": to_decimal(output.get("stck_llam")),
                "accumulated_volume": to_int(output.get("acml_vol")),
                "accumulated_amount": to_decimal(output.get("acml_tr_pbmn")),
                "weighted_average_price": to_decimal(output.get("wghn_avrg_stck_prc")),
                "foreign_net_buy_volume": to_int(output.get("frgn_ntby_qty")),
                "program_net_buy_volume": to_int(output.get("pgtr_ntby_qty")),
                "vi_classification_code": to_text(output.get("vi_cls_code")),
                "trading_halt_yn": to_text(output.get("temp_stop_yn")),
                "raw_payload": output,
            }
        )
        return row
