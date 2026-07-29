"""국내 선물옵션 현재가 시세 Collector."""

from __future__ import annotations

from ..base import BaseCollector
from ..converters import parse_yyyymmdd, to_decimal, to_int, to_text


class FuturesQuoteCollector(BaseCollector):
    path = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
    tr_id = "FHMIF10000000"

    def collect(
        self, *, futures_code: str, market_code: str, collect_cycle: str = "1MIN"
    ) -> dict[str, object]:
        requested_futures_code = to_text(futures_code)
        if not requested_futures_code:
            raise ValueError("요청 선물 단축코드가 비어 있습니다.")
        payload = self.client.get(
            path=self.path,
            tr_id=self.tr_id,
            params={"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": requested_futures_code},
        )
        output = self.output_dict(payload, "output1")
        self.require_fields(output, ("hts_kor_isnm", "futs_prpr", "futs_prdy_vrss", "prdy_vrss_sign", "futs_prdy_clpr", "futs_prdy_ctrt", "futs_oprc", "futs_hgpr", "futs_lwpr", "futs_mxpr", "futs_llam", "acml_vol", "acml_tr_pbmn", "hts_otst_stpl_qty", "otst_stpl_qty_icdc", "basis", "hts_thpr", "mrkt_basis", "futs_last_tr_date", "hts_rmnn_dynu"))
        row = self.metadata(market_code=market_code, collect_cycle=collect_cycle, trading_venue="KRX")
        # API가 데이터 기준 시각을 제공하지 않으므로 KST 실제 조회 시각을 snapshot_time으로 사용한다.
        expiration = to_text(output.get("futs_last_tr_date"))
        row.update(
            {
                "snapshot_time": row["collected_at"],
                "futures_code": requested_futures_code,
                "futures_name": to_text(output.get("hts_kor_isnm")),
                "current_price": to_decimal(output.get("futs_prpr")),
                "previous_day_difference": to_decimal(output.get("futs_prdy_vrss")),
                "previous_day_difference_sign": to_text(output.get("prdy_vrss_sign")),
                "previous_close_price": to_decimal(output.get("futs_prdy_clpr")),
                "change_rate": to_decimal(output.get("futs_prdy_ctrt")),
                "open_price": to_decimal(output.get("futs_oprc")),
                "high_price": to_decimal(output.get("futs_hgpr")),
                "low_price": to_decimal(output.get("futs_lwpr")),
                "upper_limit_price": to_decimal(output.get("futs_mxpr")),
                "lower_limit_price": to_decimal(output.get("futs_llam")),
                "base_price": to_decimal(output.get("futs_prdy_clpr")),
                "accumulated_volume": to_int(output.get("acml_vol")),
                "accumulated_amount": to_decimal(output.get("acml_tr_pbmn")),
                "open_interest": to_int(output.get("hts_otst_stpl_qty")),
                "open_interest_change": to_int(output.get("otst_stpl_qty_icdc")),
                "basis": to_decimal(output.get("basis")),
                "theoretical_price": to_decimal(output.get("hts_thpr")),
                "market_basis": to_decimal(output.get("mrkt_basis")),
                "expiration_date": parse_yyyymmdd(expiration) if expiration else None,
                "days_to_expiration": to_int(output.get("hts_rmnn_dynu")),
                "raw_payload": output,
            }
        )
        return row
