"""Production-path acceptance check for the mobile SQL analysis runner.

This script talks to the live HTTP runner on localhost.  It never handles KIS
or SEND controls.  The supplied SQL is executed only by the restricted analysis
role configured for the dashboard.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import requests
from dotenv import load_dotenv

from src.repository.database import DatabaseSettings, create_connection_pool


def submit(base_url: str, headers: dict[str, str], sql: str, *, title: str,
           source_type: str = "PASTE", filename: str | None = None) -> dict:
    response = requests.post(f"{base_url}/sql-analysis/api/run", headers=headers, timeout=30, json={
        "sql": sql, "title": title, "source_type": source_type, "filename": filename,
        "request_key": str(uuid.uuid4()),
    })
    response.raise_for_status()
    return response.json()


def wait_for(base_url: str, headers: dict[str, str], execution_id: str, timeout_seconds: int) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = requests.get(f"{base_url}/sql-analysis/api/execution",
                                params={"execution_id": execution_id}, headers=headers, timeout=30)
        response.raise_for_status()
        item = response.json()
        if item["status"] in {"SUCCEEDED", "FAILED"}:
            return item
        time.sleep(2)
    raise TimeoutError(f"execution {execution_id} did not finish")


def run_sql(base_url: str, headers: dict[str, str], sql: str, title: str, timeout_seconds: int,
            source_type: str = "PASTE", filename: str | None = None) -> dict:
    queued = submit(base_url, headers, sql, title=title, source_type=source_type, filename=filename)
    # The POST request has finished here.  Polling through a new HTTP request
    # demonstrates that the background execution does not depend on the
    # original browser/request connection.
    return wait_for(base_url, headers, queued["execution_id"], timeout_seconds)


def workbook_evidence(content: bytes) -> dict:
    with zipfile.ZipFile(__import__("io").BytesIO(content)) as package:
        workbook = ElementTree.fromstring(package.read("xl/workbook.xml"))
        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        sheets = workbook.findall("x:sheets/x:sheet", ns)
        headers = 0
        for index in range(1, len(sheets) + 1):
            xml = ElementTree.fromstring(package.read(f"xl/worksheets/sheet{index}.xml"))
            if xml.find("x:sheetData/x:row[@r='1']", ns) is not None:
                headers += 1
        return {"worksheet_count": len(sheets), "worksheets_with_header": headers}


def counts(pool) -> dict:
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT (SELECT count(*) FROM live_broker_order),
                              (SELECT count(*) FROM live_broker_fill),
                              (SELECT count(*) FROM sql_analysis_execution_history)""")
        order_count, fill_count, history_count = cur.fetchone()
        return {"broker_order": order_count, "broker_fill": fill_count, "history": history_count}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    token = os.getenv("SQL_ANALYSIS_AUTH_TOKEN", "")
    if not token:
        raise RuntimeError("SQL_ANALYSIS_AUTH_TOKEN is not configured")
    if not args.fixture.is_file():
        raise FileNotFoundError(args.fixture)
    headers = {"X-Analysis-Key": token}
    pool = create_connection_pool(DatabaseSettings.from_environment())
    before = counts(pool)
    evidence: dict[str, object] = {"before": before}
    downloaded = None
    try:
        idempotency_key = str(uuid.uuid4())
        idempotency_payload = {"sql": "SELECT pg_sleep(2), 1 AS async_ok;", "title": "async identity acceptance",
                               "source_type": "PASTE", "filename": None, "request_key": idempotency_key}
        first = requests.post(f"{args.base_url}/sql-analysis/api/run", headers=headers,
                              json=idempotency_payload, timeout=30)
        first.raise_for_status()
        retry = requests.post(f"{args.base_url}/sql-analysis/api/run", headers=headers,
                              json=idempotency_payload, timeout=30)
        retry.raise_for_status()
        overlap = requests.post(f"{args.base_url}/sql-analysis/api/run", headers=headers, timeout=30, json={
            **idempotency_payload, "request_key": str(uuid.uuid4()), "title": "overlap must fail"})
        if first.json()["execution_id"] != retry.json()["execution_id"] or overlap.status_code != 409:
            raise AssertionError("active-job/idempotency contract failed")
        async_done = wait_for(args.base_url, headers, first.json()["execution_id"], 120)
        evidence["async_idempotency"] = {"status": async_done["status"], "same_execution": True,
                                         "overlap_http_status": overlap.status_code}

        fixture_sql = args.fixture.read_text(encoding="utf-8-sig")
        started = time.monotonic()
        fixture = run_sql(args.base_url, headers, fixture_sql, "Minute MA 2x2 acceptance",
                          args.timeout_seconds, "UPLOAD", args.fixture.name)
        evidence["fixture_duration_seconds"] = round(time.monotonic() - started, 3)
        evidence["fixture"] = {key: fixture.get(key) for key in (
            "execution_id", "status", "duration_ms", "result_set_count", "total_result_rows",
            "error_sqlstate", "error_message")}
        if fixture["status"] != "SUCCEEDED":
            raise RuntimeError("representative fixture failed: " + json.dumps(evidence["fixture"], ensure_ascii=False))
        response = requests.get(f"{args.base_url}/sql-analysis/api/download/{fixture['execution_id']}",
                                headers=headers, timeout=120)
        response.raise_for_status()
        downloaded = response.content
        evidence["excel"] = {"download_bytes": len(downloaded), **workbook_evidence(downloaded)}
        if evidence["excel"]["worksheet_count"] != fixture["result_set_count"]:
            raise AssertionError("result set / worksheet count mismatch")
        if evidence["excel"]["worksheets_with_header"] != fixture["result_set_count"]:
            raise AssertionError("worksheet header missing")

        reuse = run_sql(args.base_url, headers, "SELECT count(*) AS temp_param_rows FROM tmp_mm_param;",
                        "TEMP reuse acceptance", 120)
        evidence["temp_reuse"] = {key: reuse.get(key) for key in
                                  ("status", "result_set_count", "total_result_rows", "error_sqlstate")}
        if reuse["status"] != "SUCCEEDED":
            raise AssertionError("TEMP reuse failed")

        forbidden = {
            "INSERT": "INSERT INTO sql_analysis_execution_history DEFAULT VALUES;",
            "UPDATE": "UPDATE sql_analysis_execution_history SET research_title=research_title WHERE false;",
            "DELETE": "DELETE FROM sql_analysis_execution_history WHERE false;",
            "DROP": "DROP TABLE sql_analysis_execution_history;",
            "ALTER": "ALTER TABLE sql_analysis_execution_history ADD COLUMN forbidden integer;",
            "TRUNCATE": "TRUNCATE sql_analysis_execution_history;",
            "CREATE_PERMANENT": "CREATE TABLE sql_analysis_forbidden(id integer);",
        }
        blocks = {}
        for name, sql in forbidden.items():
            item = run_sql(args.base_url, headers, sql, f"permanent {name} must fail", 120)
            blocks[name] = {"status": item["status"], "sqlstate": item.get("error_sqlstate")}
            if item["status"] != "FAILED" or item.get("error_sqlstate") not in {"42501", "25006"}:
                raise AssertionError(f"permanent {name} was not privilege-blocked: {blocks[name]}")
        evidence["permanent_write_blocks"] = blocks

        ended = requests.post(f"{args.base_url}/sql-analysis/api/session/end", headers=headers,
                              json={}, timeout=30)
        ended.raise_for_status()
        evidence["session_end"] = ended.json()
        missing = run_sql(args.base_url, headers, "SELECT count(*) FROM tmp_mm_param;",
                          "TEMP cleanup acceptance", 120)
        evidence["temp_cleanup"] = {"status": missing["status"], "sqlstate": missing.get("error_sqlstate")}
        if missing["status"] != "FAILED" or missing.get("error_sqlstate") != "42P01":
            raise AssertionError("TEMP survived analysis session end")
        evidence["after"] = counts(pool)
        if evidence["after"]["broker_order"] != before["broker_order"] or \
                evidence["after"]["broker_fill"] != before["broker_fill"]:
            raise AssertionError("broker order/fill rows changed during analysis")
        evidence["status"] = "PASS"
        print(json.dumps(evidence, ensure_ascii=False, default=str, indent=2))
        return 0
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
