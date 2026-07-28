"""시장별 투자자 매매동향(시세) Collector."""

from __future__ import annotations

from ..base import BaseCollector
from ..converters import to_decimal, to_int


INVESTOR_SOURCES = {
    "foreign": ("frgn", "ntby_qty"),
    "individual": ("prsn", "ntby_qty"),
    "institution": ("orgn", "ntby_qty"),
    "financial_investment": ("scrt", "ntby_qty"),
    "investment_trust": ("ivtr", "ntby_qty"),
    "private_fund": ("pe_fund", "ntby_vol"),
    "bank": ("bank", "ntby_qty"),
    "insurance": ("insu", "ntby_qty"),
    "merchant_bank": ("mrbn", "ntby_qty"),
    "fund": ("fund", "ntby_qty"),
    "other_organization": ("etc_orgt", "ntby_vol"),
    "other_corporation": ("etc_corp", "ntby_vol"),
}


class MarketInvestorCollector(BaseCollector):
    path = "/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market"
    tr_id = "FHPTJ04030000"

    def collect(
        self,
        *,
        market_code: str,
        fid_input_iscd: str,
        fid_input_iscd_2: str,
        collect_cycle: str = "1MIN",
    ) -> list[dict[str, object]]:
        payload = self.client.get(
            path=self.path,
            tr_id=self.tr_id,
            params={"fid_input_iscd": fid_input_iscd, "fid_input_iscd_2": fid_input_iscd_2},
        )
        required = []
        for prefix, net_suffix in INVESTOR_SOURCES.values():
            required.extend((f"{prefix}_seln_vol", f"{prefix}_shnu_vol", f"{prefix}_{net_suffix}", f"{prefix}_seln_tr_pbmn", f"{prefix}_shnu_tr_pbmn", f"{prefix}_ntby_tr_pbmn"))
        rows: list[dict[str, object]] = []
        for output in self.output_list(payload, "output"):
            self.require_fields(output, tuple(required))
            row = self.metadata(market_code=market_code, collect_cycle=collect_cycle)
            # API가 데이터 기준 시각을 제공하지 않으므로 KST 실제 조회 시각을 snapshot_time으로 사용한다.
            row["snapshot_time"] = row["collected_at"]
            for investor, (prefix, net_suffix) in INVESTOR_SOURCES.items():
                row.update(
                    {
                        f"{investor}_sell_volume": to_int(output.get(f"{prefix}_seln_vol")),
                        f"{investor}_buy_volume": to_int(output.get(f"{prefix}_shnu_vol")),
                        f"{investor}_net_buy_volume": to_int(output.get(f"{prefix}_{net_suffix}")),
                        f"{investor}_sell_amount": to_decimal(output.get(f"{prefix}_seln_tr_pbmn")),
                        f"{investor}_buy_amount": to_decimal(output.get(f"{prefix}_shnu_tr_pbmn")),
                        f"{investor}_net_buy_amount": to_decimal(output.get(f"{prefix}_ntby_tr_pbmn")),
                    }
                )
            row["raw_payload"] = output
            rows.append(row)
        return rows
