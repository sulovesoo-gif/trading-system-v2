"""Build isolated V1 Historical trades from stored KRX 1MIN RAW."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from src.minute_ma.contracts import MinuteBar
from src.minute_ma.engine import MinuteMaSignalEngine
from src.minute_ma.repository import PostgresMinuteMaRepository
from src.minute_ma.v1_historical import MinuteMaV1HistoricalReplay
from src.repository.database import DatabaseSettings, create_connection_pool


def _bars(pool, *, stock_code: str, start: datetime, end: datetime) -> tuple[MinuteBar, ...]:
    with pool.connection() as connection, connection.cursor() as cursor:
        cursor.execute("""SELECT DISTINCT ON (bar_time)
              bar_time,open_price,high_price,low_price,close_price,volume
            FROM raw_stock_minute
           WHERE stock_code=%s AND data_source='KIS' AND trading_venue='KRX'
             AND collect_cycle='1MIN' AND bar_time>=%s AND bar_time<%s
             AND bar_time::time BETWEEN TIME '09:00' AND TIME '15:30'
           ORDER BY bar_time,collected_at DESC NULLS LAST""", (stock_code, start, end))
        rows = cursor.fetchall()
    return tuple(MinuteBar(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                           int(r[5] or 0)) for r in rows)


def _commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="evaluation_from", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="evaluation_to", type=date.fromisoformat, required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--run-id", type=uuid.UUID, default=uuid.uuid4())
    args = parser.parse_args()
    if args.evaluation_to < args.evaluation_from:
        raise SystemExit("invalid replay range")
    load_dotenv(ROOT / ".env")
    if args.write and os.getenv("MINUTE_MA_V1_HISTORICAL_WRITE", "N") != "Y":
        raise SystemExit("historical write blocked")

    pool = create_connection_pool(DatabaseSettings.from_environment())
    run_id = args.run_id
    try:
        repository = PostgresMinuteMaRepository(pool, write_enabled=False)
        paths = repository.v1_policy_paths()
        if len(paths) != 2400:
            raise RuntimeError(f"V1 path invariant failed: {len(paths)}")
        raw_start = datetime(2025, 1, 1)
        raw_end = datetime.combine(args.evaluation_to + timedelta(days=1), time.min)
        signal_codes = sorted({path.signal_code for path in paths})
        execution_codes = sorted({path.execution_code for path in paths})
        source = {code: _bars(pool, stock_code=code, start=raw_start, end=raw_end)
                  for code in signal_codes}
        executions = {
            code: {bar.bar_time: bar for bar in _bars(
                pool, stock_code=code,
                start=datetime.combine(args.evaluation_from, time.min), end=raw_end)}
            for code in execution_codes
        }
        underlying = {code: {bar.bar_time: bar for bar in bars}
                      for code, bars in source.items()}
        engine = MinuteMaSignalEngine()
        prepared = {}
        for code in signal_codes:
            sample = next(path for path in paths if path.signal_code == code)
            prepared[code] = engine.prepare(path=sample, bars=source[code])
        replay = MinuteMaV1HistoricalReplay(engine=engine)

        if args.write:
            with pool.connection() as connection, connection.cursor() as cursor:
                cursor.execute("""INSERT INTO minute_ma_policy_historical_run(
                  historical_run_id,policy_version,evaluation_from,evaluation_to,provenance,
                  source_contract,code_commit,status)
                  VALUES(%s,'V1.0',%s,%s,'HISTORICAL_REPLAY',
                         'KRX_1MIN_COMPLETED_V1_POLICY',%s,'RUNNING')""",
                  (run_id, args.evaluation_from, args.evaluation_to, _commit()))
                connection.commit()
        trade_count = long_count = short_count = 0
        for number, path in enumerate(paths, 1):
            trades = replay.replay(
                path=path, prepared_points=prepared[path.signal_code],
                execution_bars=executions[path.execution_code],
                underlying_bars=underlying[path.signal_code],
                evaluation_from=args.evaluation_from, evaluation_to=args.evaluation_to)
            trade_count += len(trades)
            if path.direction == "LONG": long_count += len(trades)
            else: short_count += len(trades)
            if args.write and trades:
                rows = [(
                    run_id, path.minute_policy_path_id, t.entry_event_key,
                    t.entry_signal_time, t.entry_execution_time, t.entry_price,
                    t.underlying_entry_reference_price, t.stop_threshold_price,
                    t.exit_signal_time, t.exit_execution_time, t.exit_price, t.exit_reason,
                    t.stop_trigger_time, t.stop_trigger_underlying_close, t.basis_capital,
                    t.gross_return_pct, t.net_return_pct, t.realized_pnl,
                ) for t in trades]
                with pool.connection() as connection, connection.cursor() as cursor:
                    cursor.executemany("""INSERT INTO minute_ma_policy_historical_trade(
                      historical_run_id,minute_policy_path_id,entry_event_key,
                      entry_signal_time,entry_execution_time,entry_price,
                      underlying_entry_reference_price,stop_threshold_price,
                      exit_signal_time,exit_execution_time,exit_price,exit_reason,
                      stop_trigger_time,stop_trigger_underlying_close,basis_capital,
                      gross_return_pct,net_return_pct,realized_pnl)
                      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                      ON CONFLICT(historical_run_id,minute_policy_path_id,entry_event_key)
                      DO NOTHING""", rows)
                    connection.commit()
        if args.write:
            with pool.connection() as connection, connection.cursor() as cursor:
                cursor.execute("""UPDATE minute_ma_policy_historical_run
                   SET status='COMPLETED',path_count=%s,trade_count=%s,
                       completed_at=CURRENT_TIMESTAMP WHERE historical_run_id=%s""",
                               (len(paths), trade_count, run_id))
                connection.commit()
        print(json.dumps({
            "historical_run_id": str(run_id), "mode": "WRITE" if args.write else "NO_WRITE",
            "evaluation_from": args.evaluation_from.isoformat(),
            "evaluation_to": args.evaluation_to.isoformat(), "path_count": len(paths),
            "trade_count": trade_count, "long_trade_count": long_count,
            "short_trade_count": short_count, "forward_paper_write": 0,
            "live_write": 0, "broker_post": 0,
        }, sort_keys=True))
        return 0
    except Exception:
        if args.write:
            try:
                with pool.connection() as connection, connection.cursor() as cursor:
                    cursor.execute("""UPDATE minute_ma_policy_historical_run
                       SET status='FAILED',completed_at=CURRENT_TIMESTAMP
                       WHERE historical_run_id=%s AND status='RUNNING'""", (run_id,))
                    connection.commit()
            except Exception:
                pass
        raise
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
