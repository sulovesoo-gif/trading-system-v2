"""국내 주식 기간별 일봉 Collector."""

from __future__ import annotations

from ..base import BaseCollector
from ..converters import parse_yyyymmdd, to_decimal, to_int, to_text


class StockDailyCollector(BaseCollector):
    path = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    tr_id = "FHKST03010100"

    def collect(
        self,
        *,
        stock_code: str,
        market_code: str,
        start_date: str,
        end_date: str,
        period_division_code: str = "D",
        original_adjusted_price_code: str = "1",
        trading_venue: str = "KRX",
        collect_cycle: str = "DAILY",
    ) -> list[dict[str, object]]:
        payload = self.client.get(
            path=self.path,
            tr_id=self.tr_id,
            params={
                "FID_COND_MRKT_DIV_CODE": {"KRX": "J", "NXT": "NX", "INTEGRATED": "UN"}[trading_venue], "FID_INPUT_ISCD": stock_code,
                "FID_INPUT_DATE_1": start_date, "FID_INPUT_DATE_2": end_date,
                "FID_PERIOD_DIV_CODE": period_division_code,
                "FID_ORG_ADJ_PRC": original_adjusted_price_code,
            },
        )
        rows: list[dict[str, object]] = []
        for output in self.output_list(payload, "output2"):
            self.require_fields(output, ("stck_bsop_date", "stck_oprc", "stck_hgpr", "stck_lwpr", "stck_clpr", "acml_vol", "acml_tr_pbmn", "prdy_vrss", "prdy_vrss_sign", "mod_yn", "prtt_rate"))
            row = self.metadata(market_code=market_code, collect_cycle=collect_cycle, trading_venue=trading_venue)
            row.update(
                {
                    "trade_date": parse_yyyymmdd(output["stck_bsop_date"]),
                    "stock_code": stock_code,
                    "open_price": to_decimal(output.get("stck_oprc")),
                    "high_price": to_decimal(output.get("stck_hgpr")),
                    "low_price": to_decimal(output.get("stck_lwpr")),
                    "close_price": to_decimal(output.get("stck_clpr")),
                    "volume": to_int(output.get("acml_vol")),
                    "amount": to_decimal(output.get("acml_tr_pbmn")),
                    "previous_day_difference": to_decimal(output.get("prdy_vrss")),
                    "previous_day_difference_sign": to_text(output.get("prdy_vrss_sign")),
                    "adjusted_yn": to_text(output.get("mod_yn")),
                    "split_rate": to_decimal(output.get("prtt_rate")),
                    "raw_payload": output,
                }
            )
            rows.append(row)
        return rows
