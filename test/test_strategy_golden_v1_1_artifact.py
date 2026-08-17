"""Local integrity checks for the immutable Strategy Golden v1.1.0 export."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "strategy_golden_v1_1"


def _key(row: dict[str, object]) -> tuple[object, ...]:
    return (
        row["strategy_instance"], row["trade_date"], row["signal_time"],
        row["entry_execution_time"], row["exit_execution_time"], row["exit_reason"],
    )


def _canonical_row(row: dict[str, object]) -> str:
    normalized = dict(row)
    detail = normalized.get("reference_detail")
    if isinstance(detail, str):
        normalized["reference_detail"] = json.loads(detail)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def test_v1_1_csv_json_have_exact_same_40_rows() -> None:
    with (FIXTURE_DIR / "strategy_golden_final_v1.1.0.csv").open(encoding="utf-8", newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    payload = json.loads((FIXTURE_DIR / "strategy_golden_final_v1.1.0.json").read_text(encoding="utf-8"))
    json_rows = payload["trades"]

    assert payload["golden_version"] == "1.1.0"
    assert payload["metadata"]["golden_version"] == "1.1.0"
    assert len(csv_rows) == len(json_rows) == 40
    assert {_key(row) for row in csv_rows} == {_key(row) for row in json_rows}
    assert {_canonical_row(row) for row in csv_rows} == {_canonical_row(row) for row in json_rows}
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["row_count"] == 40
    assert manifest["csv_sha256"] == hashlib.sha256((FIXTURE_DIR / "strategy_golden_final_v1.1.0.csv").read_bytes()).hexdigest()
    assert manifest["json_sha256"] == hashlib.sha256((FIXTURE_DIR / "strategy_golden_final_v1.1.0.json").read_bytes()).hexdigest()


def test_v1_1_approved_strategy_universe() -> None:
    payload = json.loads((FIXTURE_DIR / "strategy_golden_final_v1.1.0.json").read_text(encoding="utf-8"))
    rows = payload["trades"]
    counts = Counter(row["strategy_instance"] for row in rows)
    assert counts == {
        "SAMSUNG_S1_LONG_PULLBACK_WITHIN30_EOD": 7,
        "SAMSUNG_S2_SHORT_FIXED30": 13,
        "HYNIX_S3_SHORT_3BAR": 10,
        "HYNIX_S3_SHORT_5BAR": 10,
    }
    s1_dates = {row["trade_date"] for row in rows if row["strategy_instance"].startswith("SAMSUNG_S1")}
    assert s1_dates == {
        "2026-06-01", "2026-06-09", "2026-06-17", "2026-06-22",
        "2026-07-14", "2026-07-21", "2026-08-11",
    }
    s3_3 = {_key(row)[1:4] for row in rows if row["strategy_instance"] == "HYNIX_S3_SHORT_3BAR"}
    s3_5 = {_key(row)[1:4] for row in rows if row["strategy_instance"] == "HYNIX_S3_SHORT_5BAR"}
    assert s3_3 == s3_5
