from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import re
import unittest

from src.collector.raw.domestic_stock.market_investor_collector import MarketInvestorCollector
from src.collector.raw.domestic_stock.program_collector import ProgramCollector
from src.collector.raw.domestic_stock.stock_daily_collector import StockDailyCollector
from src.collector.raw.domestic_stock.stock_execution_collector import StockExecutionCollector
from src.collector.raw.domestic_stock.stock_minute_collector import StockMinuteCollector
from src.collector.raw.domestic_stock.stock_quote_collector import StockQuoteCollector
from src.collector.raw.futures.futures_minute_collector import FuturesMinuteCollector
from src.collector.raw.futures.futures_quote_collector import FuturesQuoteCollector
from src.repository.raw_specs import RAW_SPECS


NOW = datetime(2026, 7, 28, 10, 30, 0)
ROOT = Path(__file__).parents[1]


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return self.payload


def collector(klass, payload):
    return klass(FakeClient(payload), now_provider=lambda: NOW)


class CollectorMappingTest(unittest.TestCase):
    def assert_row_matches_ddl(self, row, ddl_name):
        content = (ROOT / "database" / "ddl" / ddl_name).read_text(encoding="utf-8")
        definition = re.search(r"CREATE TABLE \w+\s*\((.*?)\n\);", content, re.DOTALL).group(1)
        columns = {
            match.group(1)
            for line in definition.splitlines()
            if (match := re.match(r"\s{4}([a-z][a-z0-9_]*)\s+(?:TIMESTAMP|DATE|VARCHAR|CHAR|BIGINT|INTEGER|NUMERIC|JSONB)", line))
        }
        columns.discard("created_at")  # DB DEFAULT로 생성되는 시각은 Collector 반환 대상이 아니다.
        self.assertEqual(set(row), columns, ddl_name)
        repository_columns = next(spec.columns for spec in RAW_SPECS.values() if spec.ddl_file == ddl_name)
        self.assertEqual(set(row), set(repository_columns), f"Repository spec mismatch: {ddl_name}")

    def test_program_mapping_and_request(self):
        api = FakeClient({"output": [{
            "bsop_hour": "102400", "stck_prpr": "1615000", "prdy_vrss": "-1000",
            "prdy_vrss_sign": "5", "prdy_ctrt": "-0.06", "acml_vol": "1,000",
            "whol_smtn_seln_vol": "11", "whol_smtn_shnu_vol": "12", "whol_smtn_ntby_qty": "1",
            "whol_smtn_seln_tr_pbmn": "100", "whol_smtn_shnu_tr_pbmn": "120",
            "whol_smtn_ntby_tr_pbmn": "20", "whol_ntby_vol_icdc": "2",
            "whol_ntby_tr_pbmn_icdc": "3",
        }]})
        rows = ProgramCollector(api, now_provider=lambda: NOW).collect(stock_code="000660", market_code="KOSPI", trading_venue="INTEGRATED")
        row = rows[0]
        self.assertEqual(row["snapshot_time"], datetime(2026, 7, 28, 10, 24))
        self.assertEqual(row["current_price"], 1615000)
        self.assertEqual(row["market_code"], "KOSPI")
        self.assertEqual(api.calls[0]["tr_id"], "FHPPG04650101")
        self.assertEqual(api.calls[0]["params"]["FID_INPUT_ISCD"], "000660")
        self.assertEqual(api.calls[0]["params"]["FID_COND_MRKT_DIV_CODE"], "UN")
        self.assert_row_matches_ddl(row, "10_raw_program.sql")

    def test_market_investor_mapping(self):
        output = {}
        for prefix, net in (
            ("frgn", "ntby_qty"), ("prsn", "ntby_qty"), ("orgn", "ntby_qty"),
            ("scrt", "ntby_qty"), ("ivtr", "ntby_qty"), ("pe_fund", "ntby_vol"),
            ("bank", "ntby_qty"), ("insu", "ntby_qty"), ("mrbn", "ntby_qty"),
            ("fund", "ntby_qty"), ("etc_orgt", "ntby_vol"), ("etc_corp", "ntby_vol"),
        ):
            output.update({f"{prefix}_seln_vol": "1", f"{prefix}_shnu_vol": "2", f"{prefix}_{net}": "1",
                           f"{prefix}_seln_tr_pbmn": "10", f"{prefix}_shnu_tr_pbmn": "20", f"{prefix}_ntby_tr_pbmn": "10"})
        rows = collector(MarketInvestorCollector, {"output": [output]}).collect(
            market_code="KOSPI", fid_input_iscd="KSP", fid_input_iscd_2="0001"
        )
        row = rows[0]
        self.assertEqual(row["private_fund_net_buy_volume"], 1)
        self.assertEqual(row["other_organization_net_buy_amount"], Decimal("10"))
        self.assertEqual(len([key for key in row if key.endswith("_net_buy_volume")]), 12)
        self.assert_row_matches_ddl(row, "11_raw_market_investor.sql")

    def test_market_investor_list_and_empty_response(self):
        output = {}
        for prefix, net in (("frgn", "ntby_qty"), ("prsn", "ntby_qty"), ("orgn", "ntby_qty"), ("scrt", "ntby_qty"), ("ivtr", "ntby_qty"), ("pe_fund", "ntby_vol"), ("bank", "ntby_qty"), ("insu", "ntby_qty"), ("mrbn", "ntby_qty"), ("fund", "ntby_qty"), ("etc_orgt", "ntby_vol"), ("etc_corp", "ntby_vol")):
            output.update({f"{prefix}_seln_vol": "1", f"{prefix}_shnu_vol": "2", f"{prefix}_{net}": "1", f"{prefix}_seln_tr_pbmn": "10", f"{prefix}_shnu_tr_pbmn": "20", f"{prefix}_ntby_tr_pbmn": "10"})
        result = collector(MarketInvestorCollector, {"output": [output, output.copy()]}).collect(market_code="KOSPI", fid_input_iscd="KSP", fid_input_iscd_2="0001")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["raw_payload"], output)
        self.assertEqual(collector(MarketInvestorCollector, {"output": []}).collect(market_code="KOSPI", fid_input_iscd="KSP", fid_input_iscd_2="0001"), [])

    def test_stock_quote_and_execution_mapping(self):
        quote = collector(StockQuoteCollector, {"output": {
            "stck_prpr": "100", "prdy_vrss": "1", "prdy_vrss_sign": "2", "prdy_ctrt": "1.0",
            "stck_oprc": "90", "stck_hgpr": "110", "stck_lwpr": "80", "stck_sdpr": "99",
            "stck_mxpr": "130", "stck_llam": "70", "acml_vol": "1000", "acml_tr_pbmn": "99900",
            "wghn_avrg_stck_prc": "98", "frgn_ntby_qty": "3", "pgtr_ntby_qty": "4",
            "vi_cls_code": "N", "temp_stop_yn": "N",
        }}).collect(stock_code="005930", market_code="KOSPI")
        self.assertEqual(quote["program_net_buy_volume"], 4)
        self.assertEqual(quote["trading_halt_yn"], "N")
        self.assert_row_matches_ddl(quote, "12_raw_stock_quote.sql")
        executions = collector(StockExecutionCollector, {"output": [{
            "stck_cntg_hour": "102959", "stck_prpr": "100", "prdy_vrss": "1",
            "prdy_vrss_sign": "2", "prdy_ctrt": "1", "cntg_vol": "5", "tday_rltv": "101.2",
        }]}).collect(stock_code="005930", market_code="KOSPI")
        execution = executions[0]
        self.assertEqual(execution["snapshot_time"], datetime(2026, 7, 28, 10, 29, 59))
        self.assertEqual(execution["execution_strength"], Decimal("101.2"))
        self.assert_row_matches_ddl(execution, "13_raw_stock_execution.sql")

    def test_stock_minute_and_daily_mapping(self):
        minute = collector(StockMinuteCollector, {"output2": [{
            "stck_bsop_date": "20260728", "stck_cntg_hour": "101500", "stck_oprc": "10",
            "stck_hgpr": "12", "stck_lwpr": "9", "stck_prpr": "11", "cntg_vol": "2", "acml_tr_pbmn": "22",
        }]}).collect(stock_code="005930", market_code="KOSPI", input_hour="103000")
        self.assertEqual(minute[0]["bar_time"], datetime(2026, 7, 28, 10, 15))
        self.assert_row_matches_ddl(minute[0], "14_raw_stock_minute.sql")
        daily = collector(StockDailyCollector, {"output2": [{
            "stck_bsop_date": "20260727", "stck_oprc": "10", "stck_hgpr": "12", "stck_lwpr": "9",
            "stck_clpr": "11", "acml_vol": "100", "acml_tr_pbmn": "1100", "prdy_vrss": "1",
            "prdy_vrss_sign": "2", "mod_yn": "N", "prtt_rate": "0",
        }]}).collect(stock_code="005930", market_code="KOSPI", start_date="20260701", end_date="20260728")
        self.assertEqual(str(daily[0]["trade_date"]), "2026-07-27")
        self.assertEqual(daily[0]["close_price"], Decimal("11"))
        self.assert_row_matches_ddl(daily[0], "15_raw_stock_daily.sql")

    def test_list_response_multiple_rows_empty_and_payload_preserved(self):
        first = {"stck_bsop_date": "20260728", "stck_cntg_hour": "101500", "stck_oprc": "10", "stck_hgpr": "12", "stck_lwpr": "9", "stck_prpr": "11", "cntg_vol": "2", "acml_tr_pbmn": "22"}
        second = {**first, "stck_cntg_hour": "101600", "stck_prpr": "12"}
        result = collector(StockMinuteCollector, {"output2": [first, second]}).collect(stock_code="005930", market_code="KOSPI", input_hour="103000")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["raw_payload"], first)
        self.assertEqual(result[1]["bar_time"], datetime(2026, 7, 28, 10, 16))
        empty = collector(StockMinuteCollector, {"output2": []}).collect(stock_code="005930", market_code="KOSPI", input_hour="103000")
        self.assertEqual(empty, [])

    def test_missing_api_field_fails_without_synthetic_value(self):
        with self.assertRaisesRegex(ValueError, "stck_prpr"):
            collector(StockQuoteCollector, {"output": {}}).collect(stock_code="005930", market_code="KOSPI")

    def test_ddl_field_mismatch_is_detected(self):
        row = {"unknown_column": 1}
        with self.assertRaises(AssertionError):
            self.assert_row_matches_ddl(row, "10_raw_program.sql")

    def test_futures_quote_and_minute_mapping(self):
        quote = collector(FuturesQuoteCollector, {"output1": {
            "hts_kor_isnm": "코스피200", "futs_prpr": "100",
            "futs_prdy_vrss": "2", "prdy_vrss_sign": "2", "futs_prdy_clpr": "98", "futs_prdy_ctrt": "2",
            "futs_oprc": "99", "futs_hgpr": "101", "futs_lwpr": "97", "futs_mxpr": "130", "futs_llam": "70",
            "acml_vol": "100", "acml_tr_pbmn": "10000", "hts_otst_stpl_qty": "20", "otst_stpl_qty_icdc": "1",
            "basis": "0.2", "hts_thpr": "99.8", "mrkt_basis": "0.1", "futs_last_tr_date": "20260910", "hts_rmnn_dynu": "44",
        }}).collect(futures_code="A01609", market_code="KOSPI200_FUTURES")
        self.assertEqual(quote["futures_code"], "A01609")
        self.assertNotIn("futs_shrn_iscd", quote["raw_payload"])
        self.assertEqual(quote["futures_name"], "코스피200")
        self.assertEqual(str(quote["expiration_date"]), "2026-09-10")
        self.assert_row_matches_ddl(quote, "16_raw_futures_quote.sql")
        minute = collector(FuturesMinuteCollector, {"output2": [{
            "stck_bsop_date": "20260728", "stck_cntg_hour": "101500", "futs_oprc": "10",
            "futs_hgpr": "12", "futs_lwpr": "9", "futs_prpr": "11", "cntg_vol": "2", "acml_tr_pbmn": "22",
        }]}).collect(futures_code="A01609", market_code="KOSPI200_FUTURES", input_date="20260728", input_hour="103000")
        self.assertEqual(minute[0]["futures_code"], "A01609")
        self.assertEqual(minute[0]["close_price"], Decimal("11"))
        self.assert_row_matches_ddl(minute[0], "17_raw_futures_minute.sql")

    def test_futures_quote_requires_nonempty_output_and_current_price(self):
        with self.assertRaisesRegex(ValueError, "futs_prpr"):
            collector(FuturesQuoteCollector, {"output1": {}}).collect(
                futures_code="A01609", market_code="KOSPI200_FUTURES"
            )
        with self.assertRaisesRegex(ValueError, "futs_prpr"):
            collector(FuturesQuoteCollector, {"output1": {"hts_kor_isnm": "F 202609"}}).collect(
                futures_code="A01609", market_code="KOSPI200_FUTURES"
            )
        with self.assertRaisesRegex(ValueError, "요청 선물 단축코드"):
            collector(FuturesQuoteCollector, {"output1": {}}).collect(
                futures_code="", market_code="KOSPI200_FUTURES"
            )

    def test_futures_minute_multiple_rows_use_requested_code_and_independent_times(self):
        latest = datetime(2026, 7, 28, 15, 30)
        output = []
        for offset in range(102):
            current = latest - timedelta(minutes=offset)
            output.append({
                "stck_bsop_date": current.strftime("%Y%m%d"),
                "stck_cntg_hour": current.strftime("%H%M%S"),
                "futs_oprc": "10", "futs_hgpr": "12", "futs_lwpr": "9",
                "futs_prpr": "11", "cntg_vol": "2", "acml_tr_pbmn": "22",
            })
        rows = collector(FuturesMinuteCollector, {"output2": output}).collect(
            futures_code="A01609", market_code="KOSPI200_FUTURES",
            input_date="20260728", input_hour="153000", hour_classification_code="30",
        )
        self.assertEqual(len(rows), 102)
        self.assertEqual(rows[0]["futures_code"], "A01609")
        self.assertEqual(rows[0]["bar_time"], latest)
        self.assertEqual(rows[-1]["bar_time"], latest - timedelta(minutes=101))
        self.assertNotIn("futs_shrn_iscd", rows[0]["raw_payload"])
