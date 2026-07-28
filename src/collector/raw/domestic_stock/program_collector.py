"""종목별 프로그램매매 추이(체결) Collector."""

from __future__ import annotations

from ..base import BaseCollector
from ..converters import combine_kst_datetime, to_decimal, to_int, to_text


class ProgramCollector(BaseCollector):
    path = "/uapi/domestic-stock/v1/quotations/program-trade-by-stock"
    tr_id = "FHPPG04650101"

    def collect(
        self, *, stock_code: str, market_code: str, collect_cycle: str = "1MIN"
    ) -> list[dict[str, object]]:
        payload = self.client.get(
            path=self.path,
            tr_id=self.tr_id,
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
        )
        rows: list[dict[str, object]] = []
        required = ("bsop_hour", "stck_prpr", "prdy_vrss", "prdy_vrss_sign", "prdy_ctrt", "acml_vol", "whol_smtn_seln_vol", "whol_smtn_shnu_vol", "whol_smtn_ntby_qty", "whol_smtn_seln_tr_pbmn", "whol_smtn_shnu_tr_pbmn", "whol_smtn_ntby_tr_pbmn", "whol_ntby_vol_icdc", "whol_ntby_tr_pbmn_icdc")
        for output in self.output_list(payload, "output"):
            self.require_fields(output, required)
            row = self.metadata(market_code=market_code, collect_cycle=collect_cycle)
            row.update(
                {
                    "snapshot_time": combine_kst_datetime(None, to_text(output.get("bsop_hour")), collection_time=row["collected_at"]),
                    "stock_code": stock_code, "current_price": to_int(output.get("stck_prpr")),
                    "previous_day_difference": to_int(output.get("prdy_vrss")), "previous_day_difference_sign": to_text(output.get("prdy_vrss_sign")),
                    "change_rate": to_decimal(output.get("prdy_ctrt")), "accumulated_volume": to_int(output.get("acml_vol")),
                    "sell_volume": to_int(output.get("whol_smtn_seln_vol")), "buy_volume": to_int(output.get("whol_smtn_shnu_vol")),
                    "net_buy_volume": to_int(output.get("whol_smtn_ntby_qty")), "sell_amount": to_int(output.get("whol_smtn_seln_tr_pbmn")),
                    "buy_amount": to_int(output.get("whol_smtn_shnu_tr_pbmn")), "net_buy_amount": to_int(output.get("whol_smtn_ntby_tr_pbmn")),
                    "net_buy_volume_change": to_int(output.get("whol_ntby_vol_icdc")), "net_buy_amount_change": to_int(output.get("whol_ntby_tr_pbmn_icdc")),
                    "raw_payload": output,
                }
            )
            rows.append(row)
        return rows
