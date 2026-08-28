"""Create a new immutable selection batch containing FULL and AFTERNOON paths."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date,datetime
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from dotenv import load_dotenv

from src.minute_ma.contracts import Axis
from src.minute_ma.selection import load_historical_csv
from src.repository.database import DatabaseSettings,create_connection_pool


AFTERNOON_AXIS=Axis.KRX_CONTINUOUS_AFTERNOON


def parser() -> argparse.ArgumentParser:
    result=argparse.ArgumentParser()
    result.add_argument("--krx-continuous-afternoon",type=Path,required=True)
    result.add_argument("--batch-id",default="MINUTE_MA_AFTERNOON_SEL_20260826_V1")
    result.add_argument("--evaluation-from",type=date.fromisoformat,required=True)
    result.add_argument("--evaluation-to",type=date.fromisoformat,required=True)
    result.add_argument("--approve",action="store_true")
    return result


def main() -> int:
    args=parser().parse_args()
    metrics=load_historical_csv(args.krx_continuous_afternoon)
    labels={str(metric.source_row.get("계산방식","")).strip() for metric in metrics.values()}
    if labels!={AFTERNOON_AXIS.value}:
        raise RuntimeError(f"unexpected Historical calculation labels: {sorted(labels)}")
    load_dotenv(ROOT/".env")
    pool=create_connection_pool(DatabaseSettings.from_environment())
    try:
      with pool.connection() as connection,connection.cursor() as cursor:
        cursor.execute("""SELECT selection_batch_id
                            FROM minute_ma_selection_batch
                           WHERE status='APPROVED'
                           ORDER BY selected_at DESC,selection_batch_id DESC
                           LIMIT 1""")
        row=cursor.fetchone()
        if row is None:
            raise RuntimeError("an APPROVED base Minute MA selection batch is required")
        base_batch_id=str(row[0])
        cursor.execute("""SELECT count(*)
                            FROM minute_ma_selection_snapshot s
                            JOIN minute_ma_path p USING(minute_path_id)
                           WHERE s.selection_batch_id=%s
                             AND right(p.data_axis,10) <> '_AFTERNOON'""",
                       (base_batch_id,))
        if cursor.fetchone()[0]!=9600:
            raise RuntimeError("latest APPROVED batch does not contain exactly 9,600 FULL paths")
        cursor.execute("""SELECT source_daily_strategy_id
                            FROM minute_ma_strategy_master
                           ORDER BY source_daily_strategy_id""")
        registry={str(value[0]) for value in cursor.fetchall()}
        if registry!=set(metrics):
            raise RuntimeError("AFTERNOON Historical identities do not match the 2,400 registry")

        cursor.execute("""INSERT INTO minute_ma_selection_batch(
          selection_batch_id,selected_at,evaluation_from,evaluation_to,metric_contract_version,
          status,source_artifacts,description,created_by)
          VALUES (%s,%s,%s,%s,'MINUTE_MA_AFTERNOON_SELECTION_V1','DRAFT',%s::jsonb,%s,%s)""",
          (args.batch_id,datetime.now(),args.evaluation_from,args.evaluation_to,
           json.dumps({AFTERNOON_AXIS.value:{"file":args.krx_continuous_afternoon.name,
                                             "bytes":args.krx_continuous_afternoon.stat().st_size}}),
           f"FULL snapshot copied from {base_batch_id}; KRX CONTINUOUS AFTERNOON >=10%; other AFTERNOON axes PENDING",
           os.environ.get("USER","CODEX")))

        cursor.execute("""INSERT INTO minute_ma_selection_snapshot(
          selection_batch_id,minute_path_id,evaluation_rank,decision_status,
          completed_trade_count,win_rate_pct,avg_net_return_pct,median_net_return_pct,
          compound_return_pct,compound_profit,final_compound_capital,max_concurrent_open,
          avg_hold_minutes,worst_trade_pct,mdd_pct,robustness_yn,recommended_amount,
          approved_amount,reason_codes,source_row)
          SELECT %s,minute_path_id,evaluation_rank,decision_status,
                 completed_trade_count,win_rate_pct,avg_net_return_pct,median_net_return_pct,
                 compound_return_pct,compound_profit,final_compound_capital,max_concurrent_open,
                 avg_hold_minutes,worst_trade_pct,mdd_pct,robustness_yn,recommended_amount,
                 approved_amount,reason_codes,source_row
            FROM minute_ma_selection_snapshot s
            JOIN minute_ma_path p USING(minute_path_id)
           WHERE s.selection_batch_id=%s
             AND right(p.data_axis,10) <> '_AFTERNOON'""",
          (args.batch_id,base_batch_id))

        ordered=sorted(metrics.values(),key=lambda metric:(
            -metric.compound_return_pct,metric.source_daily_strategy_id,
        ))
        ranks={metric.source_daily_strategy_id:index+1 for index,metric in enumerate(ordered)}
        cursor.execute("""SELECT p.minute_path_id,s.source_daily_strategy_id
                            FROM minute_ma_path p
                            JOIN minute_ma_strategy_master s USING(minute_strategy_id)
                           WHERE p.data_axis=%s
                           ORDER BY p.minute_path_id""",(AFTERNOON_AXIS.value,))
        paths=cursor.fetchall()
        if len(paths)!=2400:
            raise RuntimeError(f"expected 2,400 {AFTERNOON_AXIS.value} paths, got {len(paths)}")
        for path_id,source_id in paths:
            metric=metrics[str(source_id)]
            selected=metric.compound_return_pct>=Decimal("10.0")
            amount=Decimal("20000") if selected else None
            cursor.execute("""INSERT INTO minute_ma_selection_snapshot(
              selection_batch_id,minute_path_id,evaluation_rank,decision_status,
              completed_trade_count,win_rate_pct,avg_net_return_pct,median_net_return_pct,
              compound_return_pct,compound_profit,final_compound_capital,max_concurrent_open,
              avg_hold_minutes,worst_trade_pct,mdd_pct,robustness_yn,recommended_amount,
              approved_amount,reason_codes,source_row)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'N',%s,%s,%s,%s::jsonb)""",
              (args.batch_id,path_id,ranks[str(source_id)],'SELECTED' if selected else 'NOT_SELECTED',
               metric.completed_trade_count,metric.win_rate_pct,metric.avg_net_return_pct,
               metric.median_net_return_pct,metric.compound_return_pct,metric.compound_profit,
               metric.final_compound_capital,metric.max_concurrent_open,metric.avg_hold_minutes,
               metric.worst_trade_pct,metric.mdd_pct,amount,amount,
               ['AXIS_COMPOUND_GE_10'] if selected else ['AXIS_COMPOUND_LT_10'],
               json.dumps(metric.source_row,ensure_ascii=False)))

        cursor.execute("""INSERT INTO minute_ma_selection_snapshot(
          selection_batch_id,minute_path_id,decision_status,robustness_yn,reason_codes,source_row)
          SELECT %s,p.minute_path_id,'PENDING','N',ARRAY['HISTORICAL_PENDING'],'{}'::jsonb
            FROM minute_ma_path p
           WHERE p.data_axis IN (
             'KRX_RESET_AFTERNOON',
             'INTEGRATED_CONTINUOUS_AFTERNOON',
             'INTEGRATED_RESET_AFTERNOON')""",(args.batch_id,))
        cursor.execute("""SELECT count(*),
          count(*) FILTER (WHERE decision_status='SELECTED'),
          count(*) FILTER (WHERE decision_status='PENDING')
          FROM minute_ma_selection_snapshot WHERE selection_batch_id=%s""",(args.batch_id,))
        total,selected,pending=cursor.fetchone()
        if total!=19200 or pending!=9600:
            raise RuntimeError((total,selected,pending))
        if args.approve:
            cursor.execute("""UPDATE minute_ma_selection_batch SET status='APPROVED'
                               WHERE selection_batch_id=%s AND status='DRAFT'""",(args.batch_id,))
        connection.commit()
        print(json.dumps({"batch_id":args.batch_id,"base_batch_id":base_batch_id,
                          "snapshots":total,"selected":selected,"pending":pending,
                          "approved":args.approve},sort_keys=True))
    finally:
      pool.close()
    return 0


if __name__=="__main__":raise SystemExit(main())
