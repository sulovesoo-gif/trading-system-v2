"""Export one immutable Golden DB artifact to CSV and JSON fixtures.

The database remains canonical; this tool only performs SELECTs and writes the
versioned repository artifacts used for review and fixture validation.
"""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from pathlib import Path


VERSION = "1.1.0"
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "test" / "fixtures" / "strategy_golden_v1_1"
FIELDS = [
    "strategy_instance", "strategy_code", "strategy_version", "trade_date",
    "signal_stock_code", "signal_direction", "execution_stock_code",
    "execution_direction", "signal_time", "entry_target_time",
    "entry_execution_time", "exit_trigger_time", "exit_execution_time",
    "raw_entry_price", "raw_exit_price", "exit_reason", "shared_entry_group",
    "reference_detail", "source_definition_version",
]


def query(sql: str) -> str:
    encoded = __import__("base64").b64encode(sql.encode()).decode()
    command = (
        f"echo {encoded} | base64 -d | docker exec -i "
        "trading-system-v2-timescaledb-test psql -v ON_ERROR_STOP=1 "
        "-U trading_test -d trading_system_v2_test -At -F '\\t'"
    )
    return subprocess.run(
        ["ssh", "trading-v2", command], check=True, text=True,
        capture_output=True,
    ).stdout


def main() -> None:
    header_sql = """
        SELECT row_to_json(x)::text
        FROM (SELECT golden_version, created_at, raw_period_start, raw_period_end,
                     raw_cutoff_timestamp, signal_source_venue,
                     historical_execution_rule, provenance_status, metadata
              FROM strategy_golden_artifact
              WHERE golden_version = '1.1.0') x;
    """
    rows_sql = """
        SELECT array_to_string(ARRAY[
            strategy_instance, strategy_code, strategy_version, trade_date::text,
            signal_stock_code, signal_direction, execution_stock_code,
            execution_direction, signal_time::text, entry_target_time::text,
            entry_execution_time::text, exit_trigger_time::text,
            exit_execution_time::text, raw_entry_price::text, raw_exit_price::text,
            exit_reason, coalesce(shared_entry_group,''), reference_detail::text,
            source_definition_version
        ], E'\\t')
        FROM strategy_golden_row
        WHERE golden_version = '1.1.0'
        ORDER BY strategy_instance, trade_date, signal_time;
    """
    header_lines = [line for line in query(header_sql).splitlines() if line]
    if len(header_lines) != 1:
        raise RuntimeError("strategy_golden_artifact v1.1.0 header not found")
    rows = []
    for line in query(rows_sql).splitlines():
        values = line.split("\t")
        if len(values) != len(FIELDS):
            raise RuntimeError(f"unexpected Golden row field count: {len(values)}")
        row = dict(zip(FIELDS, values, strict=True))
        row["reference_detail"] = json.loads(row["reference_detail"])
        rows.append(row)
    if len(rows) != 40:
        raise RuntimeError(f"expected 40 Golden rows, got {len(rows)}")

    OUT.mkdir(parents=True, exist_ok=True)
    csv_path = OUT / "strategy_golden_final_v1.1.0.csv"
    json_path = OUT / "strategy_golden_final_v1.1.0.json"
    manifest_path = OUT / "manifest.json"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            csv_row = row.copy()
            csv_row["reference_detail"] = json.dumps(
                csv_row["reference_detail"], ensure_ascii=False, sort_keys=True,
            )
            writer.writerow(csv_row)
    payload = {
        "artifact_type": "strategy_golden",
        "golden_version": VERSION,
        "metadata": json.loads(header_lines[0]),
        "trades": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "golden_version": VERSION,
        "row_count": len(rows),
        "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        "json_sha256": hashlib.sha256(json_path.read_bytes()).hexdigest(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
