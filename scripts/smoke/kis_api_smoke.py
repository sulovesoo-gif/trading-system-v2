"""실거래 주문 없이 KIS 조회 API 응답 구조를 검증하는 수동 스모크 스크립트.

실행: python scripts/smoke/kis_api_smoke.py
필수 환경 변수: KIS_BASE_URL, KIS_API_KEY, KIS_API_SECRET
선택 환경 변수: KIS_TEST_STOCK_CODE, KIS_TEST_FUTURES_CODE
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False

# 스크립트를 프로젝트 루트에서 실행하지 않아도 src 패키지를 찾도록 한다.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.collector.raw.domestic_stock.market_investor_collector import MarketInvestorCollector
from src.collector.raw.domestic_stock.program_collector import ProgramCollector
from src.collector.raw.domestic_stock.stock_daily_collector import StockDailyCollector
from src.collector.raw.domestic_stock.stock_execution_collector import StockExecutionCollector
from src.collector.raw.domestic_stock.stock_minute_collector import StockMinuteCollector
from src.collector.raw.domestic_stock.stock_quote_collector import StockQuoteCollector
from src.collector.raw.futures.futures_minute_collector import FuturesMinuteCollector
from src.collector.raw.futures.futures_quote_collector import FuturesQuoteCollector
from src.collector.raw.kis_client import KISClient


KST = ZoneInfo("Asia/Seoul")
REQUIRED_ENV = ("KIS_BASE_URL", "KIS_API_KEY", "KIS_API_SECRET")
FUTURES_TEST_INSTRUMENTS = {
    "A01609": {
        "standard_code": "KR4A01690002",
        "name": "F 202609",
        "underlying": "KOSPI200",
    }
}


class RecordingClient:
    """Collector의 원래 호출을 유지하면서 마지막 응답 메타정보를 노출한다."""

    def __init__(self, client: KISClient) -> None:
        self.client = client
        self.last_payload: dict[str, Any] | None = None
        self.last_response_headers: dict[str, str] = {}
        self.last_http_status: int | None = None

    def get(self, **kwargs):
        try:
            result = self.client.get(**kwargs)
        finally:
            self.last_payload = self.client.last_payload
            self.last_response_headers = self.client.last_response_headers
            self.last_http_status = self.client.last_http_status
        return result


def now_kst() -> datetime:
    return datetime.now(KST).replace(tzinfo=None)


def load_project_env(path: Path) -> None:
    """python-dotenv가 없어도 스모크 테스트의 필요한 환경 변수만 읽는다."""
    if load_dotenv(path):
        return
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in REQUIRED_ENV or key in {"KIS_TEST_STOCK_CODE", "KIS_TEST_FUTURES_CODE"}:
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def summarize_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"top_level_keys": [], "output_locations": {}, "object_fields": {}, "empty_fields": [], "sign_fields": [], "decimal_fields": [], "time_fields": []}
    output_keys = sorted(key for key in payload if key.startswith("output"))
    output_locations = {
        key: "목록" if isinstance(payload.get(key), list) else "객체" if isinstance(payload.get(key), dict) else "없음"
        for key in output_keys
    }
    object_fields = {}
    objects = []
    for key in output_keys:
        value = payload.get(key)
        if isinstance(value, dict):
            objects.append(value)
            object_fields[key] = sorted(value)
        elif isinstance(value, list):
            objects.extend(item for item in value if isinstance(item, dict))
            object_fields[key] = sorted({field for item in value if isinstance(item, dict) for field in item})
    fields = sorted({field for item in objects for field in item})
    first_values = {field: next((item[field] for item in objects if field in item), None) for field in fields}
    return {
        "top_level_keys": sorted(payload),
        "output_locations": output_locations,
        "object_fields": object_fields,
        "field_names": fields,
        "empty_fields": sorted(field for field, value in first_values.items() if isinstance(value, str) and not value.strip()),
        "sign_fields": sorted(field for field in fields if "sign" in field),
        "decimal_fields": sorted(field for field, value in first_values.items() if isinstance(value, str) and "." in value),
        "time_fields": sorted(field for field in fields if any(token in field for token in ("date", "hour", "time"))),
    }


def raw_payloads(rows: Any) -> list[dict[str, Any]]:
    source_rows = rows if isinstance(rows, list) else [rows]
    return [row["raw_payload"] for row in source_rows]


def summarize_row_times(rows: Any, time_field: str) -> str:
    """목록 RAW 행의 원문 시각과 snapshot_time 변환 결과를 요약한다."""
    if not isinstance(rows, list):
        return "목록 응답이 아니므로 행별 시각 검증 대상이 아닙니다."

    raw_times = [str(row.get("raw_payload", {}).get(time_field, "")) for row in rows]
    timestamp_field = "snapshot_time" if all("snapshot_time" in row for row in rows) else "bar_time"
    snapshot_times = [row.get(timestamp_field) for row in rows]
    raw_time_values = [value for value in raw_times if value]
    snapshot_matches = all(
        snapshot is not None
        and raw_time
        and snapshot.strftime("%H%M%S") == raw_time
        for snapshot, raw_time in zip(snapshot_times, raw_times)
    )
    if len(raw_time_values) < 2:
        ordering = "판단 불가"
    elif raw_time_values == sorted(raw_time_values, reverse=True):
        ordering = "최신순(내림차순)"
    elif raw_time_values == sorted(raw_time_values):
        ordering = "과거순(오름차순)"
    else:
        ordering = "혼합 또는 자정 경계"
    return (
        f"행별 {time_field} 존재={len(raw_time_values)}/{len(rows)}, "
        f"서로 다른 시각={len(set(raw_time_values))}, 정렬={ordering}, "
        f"{timestamp_field} 시각 일치={snapshot_matches}"
    )


def run_case(
    name: str,
    tr_id: str,
    callback: Callable[[], Any],
    client: RecordingClient,
    *,
    repeat: bool = False,
    row_time_field: str | None = None,
) -> bool:
    try:
        rows = callback()
        first_payloads = raw_payloads(rows)
        summary = summarize_payload(client.last_payload)
        count = len(rows) if isinstance(rows, list) else 1
        print(f"[{tr_id}] {name}: 성공 | HTTP={client.last_http_status} | RAW 행={count} | 객체={summary['output_locations']} | tr_cont={client.last_response_headers.get('tr_cont', '') or '없음'}")
        print(f"  최상위 키={summary['top_level_keys']}")
        print(f"  객체별 필드={summary['object_fields']}")
        print(f"  빈값 필드={summary['empty_fields']} | 부호 필드={summary['sign_fields']} | 소수 표현 필드={summary['decimal_fields']} | 날짜·시각 필드={summary['time_fields']}")
        if row_time_field:
            print(f"  행별 시각 검증: {summarize_row_times(rows, row_time_field)}")
        if repeat:
            repeated_rows = callback()
            duplicated = first_payloads == raw_payloads(repeated_rows)
            repeated_count = len(repeated_rows) if isinstance(repeated_rows, list) else 1
            print(f"  동일 요청 반복: RAW 행={repeated_count}, 원문 객체 완전 동일={duplicated}")
        return True
    except Exception as error:
        payload = client.last_payload or {}
        print(f"[{tr_id}] {name}: 실패 | {type(error).__name__}: {error}")
        if payload:
            print(f"  KIS 상태: rt_cd={payload.get('rt_cd')}, msg_cd={payload.get('msg_cd')}, msg1={payload.get('msg1')}")
            summary = summarize_payload(payload)
            print(f"  최상위 키={summary['top_level_keys']} | 객체={summary['output_locations']} | 객체별 필드={summary['object_fields']}")
        return False


def main() -> int:
    load_project_env(PROJECT_ROOT / ".env")
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        print(f"스킵: KIS 스모크 테스트에 필요한 환경 변수가 없습니다: {', '.join(missing)}")
        return 0

    stock_code = os.getenv("KIS_TEST_STOCK_CODE")
    futures_code = os.getenv("KIS_TEST_FUTURES_CODE")
    client = RecordingClient(KISClient())
    current = now_kst()
    start_date = (current - timedelta(days=7)).strftime("%Y%m%d")
    end_date = current.strftime("%Y%m%d")
    input_hour = current.strftime("%H%M%S")
    passed = []

    if stock_code:
        passed.extend([
            run_case("종목별 프로그램매매", "FHPPG04650101", lambda: ProgramCollector(client).collect(stock_code=stock_code, market_code="KOSPI"), client, repeat=True, row_time_field="bsop_hour"),
            run_case("주식현재가 시세", "FHKST01010100", lambda: StockQuoteCollector(client).collect(stock_code=stock_code, market_code="KOSPI"), client),
            run_case("주식현재가 체결", "FHKST01010300", lambda: StockExecutionCollector(client).collect(stock_code=stock_code, market_code="KOSPI"), client, repeat=True, row_time_field="stck_cntg_hour"),
            run_case("주식당일분봉조회", "FHKST03010200", lambda: StockMinuteCollector(client).collect(stock_code=stock_code, market_code="KOSPI", input_hour=input_hour), client, repeat=True),
            run_case("국내주식기간별시세", "FHKST03010100", lambda: StockDailyCollector(client).collect(stock_code=stock_code, market_code="KOSPI", start_date=start_date, end_date=end_date), client, repeat=True),
        ])
    else:
        print("스킵: KIS_TEST_STOCK_CODE가 없어 국내주식 API 5건을 호출하지 않았습니다.")

    passed.append(run_case("시장별 투자자매매동향", "FHPTJ04030000", lambda: MarketInvestorCollector(client).collect(market_code="KOSPI", fid_input_iscd="KSP", fid_input_iscd_2="0001"), client))

    if futures_code:
        instrument = FUTURES_TEST_INSTRUMENTS.get(futures_code)
        if instrument:
            print(
                "[선물 테스트 기준] "
                f"단축코드={futures_code} | 표준코드={instrument['standard_code']} | "
                f"종목명={instrument['name']} | 기초자산={instrument['underlying']}"
            )
        else:
            print(f"[선물 테스트 기준] 단축코드={futures_code} | 마스터 정보 미등록")
        passed.extend([
            run_case("선물옵션 시세", "FHMIF10000000", lambda: FuturesQuoteCollector(client).collect(futures_code=futures_code, market_code="KOSPI200_FUTURES"), client),
            run_case("선물옵션 분봉조회", "FHKIF03020200", lambda: FuturesMinuteCollector(client).collect(futures_code=futures_code, market_code="KOSPI200_FUTURES", input_date=end_date, input_hour="153000", hour_classification_code="30", previous_data_include_yn="N", fake_tick_include_yn="N"), client, repeat=True, row_time_field="stck_cntg_hour"),
        ])
    else:
        print("스킵: KIS_TEST_FUTURES_CODE가 없어 국내선물 API 2건을 호출하지 않았습니다.")

    return 0 if all(passed) else 1


if __name__ == "__main__":
    sys.exit(main())
