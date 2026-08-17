"""Read-only Golden v1.1 RAW -> Core -> Decision -> artifact set validation."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.strategy_core import HistoricalDataProvider, HistoricalGoldenValidationAdapter, StrategyCore, strategy_from_registry_row
from src.strategy_core.bars import CompletedBar


FIXTURE = ROOT / "test" / "fixtures" / "strategy_golden_v1_1" / "strategy_golden_final_v1.1.0.json"
START, END = "2026-05-27", "2026-08-14"


def raw_rows() -> list[CompletedBar | tuple[str, CompletedBar]]:
    sql = f"""
      SELECT stock_code,bar_time,open_price,high_price,low_price,close_price,volume
      FROM raw_stock_minute
      WHERE collect_cycle='1MIN'
        AND ((stock_code IN ('005930','000660') AND trading_venue='INTEGRATED')
          OR (stock_code IN ('0193W0','0193L0','0197X0') AND trading_venue='KRX'))
        AND bar_time::date BETWEEN DATE '{START}' AND DATE '{END}'
        AND bar_time::time BETWEEN TIME '09:00' AND TIME '15:19'
      ORDER BY stock_code,bar_time;
    """
    encoded = base64.b64encode(sql.encode()).decode()
    command = (
        f"echo {encoded} | base64 -d | docker exec -i trading-system-v2-timescaledb-test "
        "psql -v ON_ERROR_STOP=1 -U trading_test -d trading_system_v2_test -At -F '|'"
    )
    completed = subprocess.run(["ssh", "trading-v2", command], check=True, text=True, capture_output=True)
    rows = []
    for line in completed.stdout.splitlines():
        values = line.split("|")
        if len(values) != 7:
            raise RuntimeError(f"unexpected RAW export row: {line!r}")
        stock, at, opened, high, low, close, volume = values
        rows.append((stock, CompletedBar(datetime.fromisoformat(at), float(opened), float(high), float(low), float(close), float(volume))))
    return rows


def definition(*, instance: str, code: str, signal_stock: str, signal_direction: str, execution_stock: str) -> StrategyCore:
    row = {
        "strategy_id": None, "strategy_code": code, "signal_stock_code": signal_stock,
        "signal_direction": signal_direction, "execution_stock_code": execution_stock,
        "execution_direction": "LONG", "entry_variant": "1.1.0", "exit_variant": "1.1.0",
        "entry_params": {"move_threshold": .008, "rvol_threshold": 2.0} if code == "S3_VOLUME_CLIMAX_REVERSAL" else {},
        "exit_params": {},
    }
    return StrategyCore(strategy_from_registry_row(row, strategy_instance=instance, strategy_version="1.1.0"))


def comparable_generated(trade) -> tuple:
    return (
        trade.strategy_instance, trade.trade_date, trade.signal_time.isoformat(),
        trade.entry_execution_time.isoformat(), trade.exit_execution_time.isoformat(),
        f"{trade.raw_entry_price:.2f}", f"{trade.raw_exit_price:.2f}", trade.exit_reason,
        trade.shared_entry_group or "",
    )


def comparable_golden(row: dict) -> tuple:
    normalized = lambda value: datetime.fromisoformat(value.replace(" ", "T")).isoformat()
    return (
        row["strategy_instance"], row["trade_date"], normalized(row["signal_time"]),
        normalized(row["entry_execution_time"]), normalized(row["exit_execution_time"]),
        f"{float(row['raw_entry_price']):.2f}", f"{float(row['raw_exit_price']):.2f}", row["exit_reason"],
        row["shared_entry_group"] or "",
    )


def main() -> None:
    grouped: dict[str, list[CompletedBar]] = defaultdict(list)
    for stock, bar in raw_rows():
        grouped[stock].append(bar)
    provider = HistoricalDataProvider(grouped)
    adapter = HistoricalGoldenValidationAdapter(provider)
    dates = sorted({bar.time.date().isoformat() for bar in grouped["005930"]} | {bar.time.date().isoformat() for bar in grouped["000660"]})
    cores = (
        definition(instance="SAMSUNG_S1_LONG_PULLBACK_WITHIN30_EOD", code="S1_OR_PULLBACK_RESTART", signal_stock="005930", signal_direction="LONG", execution_stock="0193W0"),
        definition(instance="SAMSUNG_S2_SHORT_FIXED30", code="S2_FAILED_OR_VWAP", signal_stock="005930", signal_direction="SHORT", execution_stock="0193L0"),
        definition(instance="HYNIX_S3_SHORT_3BAR", code="S3_VOLUME_CLIMAX_REVERSAL", signal_stock="000660", signal_direction="SHORT", execution_stock="0197X0"),
        definition(instance="HYNIX_S3_SHORT_5BAR", code="S3_VOLUME_CLIMAX_REVERSAL", signal_stock="000660", signal_direction="SHORT", execution_stock="0197X0"),
    )
    generated = [trade for core in cores for trade in adapter.replay(core, dates)]
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))["trades"]
    generated_set = {comparable_generated(trade) for trade in generated}
    golden_set = {comparable_golden(row) for row in golden}
    summary = {
        "generated": len(generated_set), "golden": len(golden_set),
        "missing": len(golden_set - generated_set), "extra": len(generated_set - golden_set),
        "mismatch": 0, "by_instance": {instance: sum(1 for row in generated if row.strategy_instance == instance) for instance in sorted({row.strategy_instance for row in generated})},
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if summary["missing"] or summary["extra"] or len(generated_set) != 40:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
