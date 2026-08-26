"""Export one official Minute MA Historical axis from TEST/operating RAW.

The signal engine and PAPER execution contract are imported from production
code. This script does not write database state.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from src.minute_ma.contracts import Axis,MinuteBar
from src.minute_ma.engine import MinuteMaSignalEngine
from src.minute_ma.historical import HISTORICAL_COLUMNS,MinuteMaHistoricalReplay,result_row
from src.minute_ma.repository import PostgresMinuteMaRepository
from src.repository.database import DatabaseSettings,create_connection_pool


def _bars(rows) -> tuple[MinuteBar,...]:
    return tuple(MinuteBar(
        row[0],float(row[1]),float(row[2]),float(row[3]),float(row[4]),int(row[5] or 0),
    ) for row in rows)


def source_bars(connection, *, stock_code: str, axis: Axis,
                evaluation_from: date, evaluation_to: date) -> tuple[MinuteBar,...]:
    start,end=axis.session
    range_start=datetime.combine(evaluation_from,time.min)
    range_end=datetime.combine(evaluation_to+timedelta(days=1),time.min)
    with connection.cursor() as cursor:
        if axis.continuity.value == "CONTINUOUS":
            cursor.execute("""WITH prior AS (
              SELECT DISTINCT ON (bar_time) bar_time,open_price,high_price,low_price,close_price,volume
                FROM raw_stock_minute
               WHERE stock_code=%s AND data_source='KIS' AND trading_venue=%s
                 AND collect_cycle='1MIN' AND bar_time<%s
                 AND bar_time::time BETWEEN %s AND %s
               ORDER BY bar_time DESC,collected_at DESC NULLS LAST LIMIT 50
            ), period AS (
              SELECT DISTINCT ON (bar_time) bar_time,open_price,high_price,low_price,close_price,volume
                FROM raw_stock_minute
               WHERE stock_code=%s AND data_source='KIS' AND trading_venue=%s
                 AND collect_cycle='1MIN' AND bar_time>=%s AND bar_time<%s
                 AND bar_time::time BETWEEN %s AND %s
               ORDER BY bar_time,collected_at DESC NULLS LAST
            ) SELECT * FROM prior UNION ALL SELECT * FROM period ORDER BY bar_time""",
              (stock_code,axis.market_source.value,range_start,start,end,
               stock_code,axis.market_source.value,range_start,range_end,start,end))
        else:
            cursor.execute("""SELECT DISTINCT ON (bar_time)
                     bar_time,open_price,high_price,low_price,close_price,volume
                FROM raw_stock_minute
               WHERE stock_code=%s AND data_source='KIS' AND trading_venue=%s
                 AND collect_cycle='1MIN' AND bar_time>=%s AND bar_time<%s
                 AND bar_time::time BETWEEN %s AND %s
               ORDER BY bar_time,collected_at DESC NULLS LAST""",
              (stock_code,axis.market_source.value,range_start,range_end,start,end))
        return _bars(cursor.fetchall())


def execution_bars(connection, *, stock_code: str,
                   evaluation_from: date, evaluation_to: date) -> dict[datetime,MinuteBar]:
    range_start=datetime.combine(evaluation_from,time.min)
    range_end=datetime.combine(evaluation_to+timedelta(days=1),time.min)
    with connection.cursor() as cursor:
        cursor.execute("""SELECT DISTINCT ON (bar_time)
                 bar_time,open_price,high_price,low_price,close_price,volume
            FROM raw_stock_minute
           WHERE stock_code=%s AND data_source='KIS' AND trading_venue='KRX'
             AND collect_cycle='1MIN' AND bar_time>=%s AND bar_time<%s
             AND bar_time::time BETWEEN TIME '09:00' AND TIME '15:19'
           ORDER BY bar_time,collected_at DESC NULLS LAST""",
          (stock_code,range_start,range_end))
        return {bar.bar_time:bar for bar in _bars(cursor.fetchall())}


def parser() -> argparse.ArgumentParser:
    result=argparse.ArgumentParser()
    result.add_argument("--axis",choices=[axis.value for axis in Axis],required=True)
    result.add_argument("--evaluation-from",type=date.fromisoformat,required=True)
    result.add_argument("--evaluation-to",type=date.fromisoformat,required=True)
    result.add_argument("--output",type=Path,required=True)
    result.add_argument("--offline-dir",type=Path)
    result.add_argument("--label-source",type=Path,
                        help="Existing official CSV used only for strategy display labels")
    return result


def main() -> int:
    args=parser().parse_args()
    if args.evaluation_to<args.evaluation_from:
        raise SystemExit("evaluation-to must be on or after evaluation-from")
    axis=Axis(args.axis)
    labels=_strategy_labels(args.label_source) if args.label_source else {}
    pool=None
    try:
        if args.offline_dir:
            paths=_offline_paths(args.offline_dir/"paths.csv",axis)
            sources=_offline_bars(args.offline_dir/"source.csv")
            executions=_offline_bars(args.offline_dir/"execution.csv")
        else:
            from dotenv import load_dotenv
            load_dotenv(ROOT/".env")
            pool=create_connection_pool(DatabaseSettings.from_environment())
            repository=PostgresMinuteMaRepository(pool,write_enabled=False)
            paths=repository.paths(axis)
            with pool.connection() as connection:
                sources={code:source_bars(
                    connection,stock_code=code,axis=axis,
                    evaluation_from=args.evaluation_from,evaluation_to=args.evaluation_to,
                ) for code in {path.signal_code for path in paths}}
                executions={code:execution_bars(
                    connection,stock_code=code,
                    evaluation_from=args.evaluation_from,evaluation_to=args.evaluation_to,
                ) for code in {path.execution_code for path in paths}}
        if len(paths)!=2400:
            raise RuntimeError(f"{axis.value} requires exactly 2400 paths; got {len(paths)}")
        by_signal=defaultdict(list)
        for path in paths:by_signal[path.signal_code].append(path)
        engine=MinuteMaSignalEngine();replay=MinuteMaHistoricalReplay(engine=engine)
        rows=[]
        for signal_code,signal_paths in sorted(by_signal.items()):
            bars=sources[signal_code]
            prepared=engine.prepare(path=signal_paths[0],bars=bars)
            for path in signal_paths:
                result=replay.replay(
                    source_daily_strategy_id=_source_strategy_id(path),
                    path=path,prepared_points=prepared,
                    execution_bars=executions[path.execution_code],
                    evaluation_from=args.evaluation_from,evaluation_to=args.evaluation_to,
                )
                row=result_row(result)
                row["신호종목"]=labels.get(result.source_daily_strategy_id,row["신호종목"])
                rows.append(row)
        rows.sort(key=lambda row:(-row["누적복리수익률_pct"],row["전략id"]))
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open("w",encoding="utf-8-sig",newline="") as handle:
            writer=csv.DictWriter(handle,fieldnames=HISTORICAL_COLUMNS)
            writer.writeheader();writer.writerows(rows)
        print({"axis":axis.value,"rows":len(rows),"output":str(args.output),
               "evaluation_from":args.evaluation_from.isoformat(),
               "evaluation_to":args.evaluation_to.isoformat()})
        return 0
    finally:
        if pool is not None:pool.close()


def _source_strategy_id(path) -> str:
    source_id=path.source_daily_strategy_id
    if source_id is None:
        raise RuntimeError("path repository did not supply source_daily_strategy_id")
    return str(source_id)


def _offline_paths(path: Path,axis: Axis):
    from src.minute_ma.contracts import MinuteMaPath
    with path.open("r",encoding="utf-8-sig",newline="") as handle:
        rows=list(csv.DictReader(handle))
    return tuple(MinuteMaPath(
        int(row["minute_path_id"]),row["path_key"],Axis(row["data_axis"]),
        row["signal_code"],row["execution_code"],row["direction"],
        int(row["entry_fast_ma"]),int(row["entry_slow_ma"]),
        int(row["exit_fast_ma"]),int(row["exit_slow_ma"]),
        None if row["trend_ma"]=="" else int(row["trend_ma"]),
        row["source_daily_strategy_id"],
    ) for row in rows if row["data_axis"]==axis.value)


def _offline_bars(path: Path):
    grouped=defaultdict(list)
    with path.open("r",encoding="utf-8-sig",newline="") as handle:
        for row in csv.DictReader(handle):
            grouped[row["stock_code"]].append(MinuteBar(
                datetime.fromisoformat(row["bar_time"]),
                float(row["open_price"]),float(row["high_price"]),
                float(row["low_price"]),float(row["close_price"]),int(row["volume"] or 0),
            ))
    return {stock_code:(
        {bar.bar_time:bar for bar in bars} if path.name=="execution.csv" else tuple(bars)
    ) for stock_code,bars in grouped.items()}


def _strategy_labels(path: Path) -> dict[str,str]:
    with path.open("r",encoding="utf-8-sig",newline="") as handle:
        rows=list(csv.DictReader(handle))
    labels={row["전략id"]:row["신호종목"] for row in rows}
    if len(labels)!=2400:
        raise RuntimeError(f"label source must contain 2,400 unique strategies; got {len(labels)}")
    return labels


if __name__=="__main__":raise SystemExit(main())
