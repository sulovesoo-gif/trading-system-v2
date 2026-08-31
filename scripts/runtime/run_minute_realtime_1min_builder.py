"""Run the isolated H0STCNT0 research 1MIN builder."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.flow_raw.realtime_minute import build_realtime_minute_bars
from src.flow_raw.realtime_minute_repository import RealtimeMinuteRepository
from src.repository.database import DatabaseSettings, create_connection_pool

KST = ZoneInfo("Asia/Seoul")


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered=sorted(values);index=(len(ordered)-1)*fraction;lower=int(index);upper=min(lower+1,len(ordered)-1)
    return ordered[lower]+(ordered[upper]-ordered[lower])*(index-lower)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--replay-date", help="read-only YYYY-MM-DD replay summary")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    grace_ms = max(0, int(os.getenv("REALTIME_1MIN_FINALIZE_GRACE_MS", "2000")))
    poll_ms = max(50, int(os.getenv("REALTIME_1MIN_POLL_MS", "250")))
    pool = create_connection_pool(DatabaseSettings.from_environment())
    repository = RealtimeMinuteRepository(pool)
    try:
        if args.replay_date:
            start = datetime.fromisoformat(args.replay_date)
            ticks = repository.execution_ticks(since=start, until=start + timedelta(days=1))
            bars = build_realtime_minute_bars(ticks, now=start + timedelta(days=1), grace_ms=grace_ms)
            with pool.connection() as connection, connection.cursor() as cursor:
                cursor.execute("""SELECT stock_code,bar_time,open_price,high_price,low_price,close_price,volume
                  FROM raw_stock_minute WHERE data_source='KIS' AND trading_venue='KRX'
                   AND collect_cycle='1MIN' AND bar_time>=%s AND bar_time<%s""",
                  (start,start+timedelta(days=1)))
                rest={(str(row[0]),row[1]):row[2:] for row in cursor.fetchall()}
            summaries={}
            for symbol in sorted({bar.stock_code for bar in bars}):
                regular=[bar for bar in bars if bar.stock_code==symbol and
                         datetime.strptime("09:00","%H:%M").time() <= bar.bar_time.time() <=
                         datetime.strptime("15:17","%H:%M").time()]
                common=[bar for bar in regular if (symbol,bar.bar_time) in rest]
                ohlc_match=sum(all(int(value)==int(expected) for value,expected in zip(
                    (bar.open_price,bar.high_price,bar.low_price,bar.close_price),
                    rest[(symbol,bar.bar_time)][:4])) for bar in common)
                delays=[bar.watermark_delay_ms for bar in regular if bar.finalize_reason=="NEXT_MINUTE_EVENT"]
                summaries[symbol]={"bars":len(regular),"common":len(common),"ohlc_match":ohlc_match,
                  "ohlc_match_pct":round(100*ohlc_match/len(common),4) if common else None,
                  "rest_mismatch":len(common)-ohlc_match,"watermark_p50_ms":round(percentile(delays,.5) or 0,3),
                  "watermark_p95_ms":round(percentile(delays,.95) or 0,3)}
            from src.minute_ma.contracts import MinuteBar
            from src.minute_ma.engine import MinuteMaSignalEngine
            from src.minute_ma.repository import PostgresMinuteMaRepository
            policy_paths=PostgresMinuteMaRepository(pool,write_enabled=False).v1_policy_paths()
            signal_counts={}
            engine=MinuteMaSignalEngine()
            for symbol in ('005930','000660'):
                source=[MinuteBar(bar.bar_time,bar.open_price,bar.high_price,bar.low_price,bar.close_price,
                    bar.volume or 0,bar.finalized_at,bar.quality_status!='INCOMPLETE','KIS_H0STCNT0_REALTIME')
                    for bar in bars if bar.stock_code==symbol]
                signal_counts[symbol]=sum(len(engine.evaluate(path=path,bars=source))
                    for path in policy_paths if path.signal_code==symbol)
            print({"ticks": len(ticks), "bars": len(bars), "summaries": summaries,
                   "realtime_ma_signal_events":signal_counts})
            return 0
        inserted,audited=repository.run_startup_backlog(
            now=datetime.now(KST).replace(tzinfo=None),grace_ms=grace_ms)
        logging.info("realtime 1MIN startup replay inserted=%d rest_audited=%d",inserted,audited)
        while True:
            now = datetime.now(KST).replace(tzinfo=None)
            inserted, audited = repository.run_recent(now=now, grace_ms=grace_ms)
            if inserted:
                logging.info("realtime 1MIN inserted=%d rest_audited=%d", inserted, audited)
            if args.once:
                return 0
            time.sleep(poll_ms / 1000)
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
