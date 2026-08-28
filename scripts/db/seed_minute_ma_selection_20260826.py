from __future__ import annotations

import argparse,json,os
from datetime import date,datetime
from decimal import Decimal
from pathlib import Path

from src.minute_ma.contracts import Axis
from src.minute_ma.selection import AVAILABLE_AXES,build_selection_rows
from src.repository.database import DatabaseSettings,create_connection_pool


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser()
    p.add_argument("--krx-continuous",type=Path,required=True)
    p.add_argument("--krx-reset",type=Path,required=True)
    p.add_argument("--integrated-continuous",type=Path,required=True)
    p.add_argument("--batch-id",default="MINUTE_MA_SEL_20260826_V1")
    p.add_argument("--evaluation-from",type=date.fromisoformat,required=True)
    p.add_argument("--evaluation-to",type=date.fromisoformat,required=True)
    p.add_argument("--approve",action="store_true")
    p.add_argument("--apply-live-operation",action="store_true")
    return p


def main() -> int:
    args=parser().parse_args()
    if args.apply_live_operation and not args.approve:
        raise SystemExit("--apply-live-operation requires --approve")
    files={Axis.KRX_CONTINUOUS:args.krx_continuous,Axis.KRX_RESET:args.krx_reset,
           Axis.INTEGRATED_CONTINUOUS:args.integrated_continuous}
    loaded=build_selection_rows(files)  # fail before any DB write
    robustness={sid:all(loaded[a][sid].compound_return_pct>0 for a in AVAILABLE_AXES)
                for sid in loaded[Axis.KRX_CONTINUOUS]}
    pool=create_connection_pool(DatabaseSettings.from_environment())
    try:
      with pool.connection() as conn,conn.cursor() as cur:
        cur.execute("SELECT source_daily_strategy_id,count(*) FROM minute_ma_strategy_master GROUP BY 1")
        db_ids={str(row[0]) for row in cur.fetchall()}
        if db_ids!=set(robustness):
            raise RuntimeError("historical artifacts do not exactly match the DB 2400 semantic registry")
        artifacts={axis.value:{"file":path.name,"bytes":path.stat().st_size} for axis,path in files.items()}
        cur.execute("""INSERT INTO minute_ma_selection_batch(
          selection_batch_id,selected_at,evaluation_from,evaluation_to,metric_contract_version,
          status,source_artifacts,description,created_by)
          VALUES (%s,%s,%s,%s,'MINUTE_MA_SELECTION_V1','DRAFT',%s::jsonb,%s,%s)""",
          (args.batch_id,datetime.now(),args.evaluation_from,args.evaluation_to,json.dumps(artifacts),
           'Initial 3-axis >=10% selection; INTEGRATED_RESET remains PENDING',os.environ.get('USER','CODEX')))
        cur.execute("""SELECT p.minute_path_id,p.data_axis,s.source_daily_strategy_id
                         FROM minute_ma_path p JOIN minute_ma_strategy_master s USING(minute_strategy_id)
                        ORDER BY p.minute_path_id""")
        path_rows=cur.fetchall()
        if len(path_rows)!=9600: raise RuntimeError(f"expected 9600 paths, got {len(path_rows)}")
        ranks={}
        for axis in AVAILABLE_AXES:
            ordered=sorted(loaded[axis].values(),key=lambda m:(-m.compound_return_pct,m.source_daily_strategy_id))
            ranks[axis]={m.source_daily_strategy_id:i+1 for i,m in enumerate(ordered)}
        for path_id,axis_value,sid in path_rows:
            axis=Axis(str(axis_value))
            robust='Y' if robustness[str(sid)] else 'N'
            if axis is Axis.INTEGRATED_RESET:
                cur.execute("""INSERT INTO minute_ma_selection_snapshot(
                  selection_batch_id,minute_path_id,decision_status,robustness_yn,reason_codes,source_row)
                  VALUES (%s,%s,'PENDING',%s,ARRAY['HISTORICAL_PENDING'],'{}'::jsonb)""",
                  (args.batch_id,path_id,robust))
                continue
            metric=loaded[axis][str(sid)]
            selected=metric.compound_return_pct>=Decimal('10.0')
            reasons=['AXIS_COMPOUND_GE_10'] if selected else ['AXIS_COMPOUND_LT_10']
            if robust: reasons.append('ROBUST_THREE_AXIS_POSITIVE')
            approved_amount=Decimal("20000") if selected else None
            cur.execute("""INSERT INTO minute_ma_selection_snapshot(
              selection_batch_id,minute_path_id,evaluation_rank,decision_status,
              completed_trade_count,win_rate_pct,avg_net_return_pct,median_net_return_pct,
              compound_return_pct,compound_profit,final_compound_capital,max_concurrent_open,
              avg_hold_minutes,worst_trade_pct,mdd_pct,robustness_yn,recommended_amount,
              approved_amount,reason_codes,source_row)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
              (args.batch_id,path_id,ranks[axis][str(sid)],'SELECTED' if selected else 'NOT_SELECTED',
               metric.completed_trade_count,metric.win_rate_pct,metric.avg_net_return_pct,
               metric.median_net_return_pct,metric.compound_return_pct,metric.compound_profit,
               metric.final_compound_capital,metric.max_concurrent_open,metric.avg_hold_minutes,
               metric.worst_trade_pct,metric.mdd_pct,robust,approved_amount,approved_amount,
               reasons,json.dumps(metric.source_row,ensure_ascii=False)))
        cur.execute("""SELECT count(*),count(*) FILTER(WHERE decision_status='PENDING'),
          count(*) FILTER(WHERE decision_status='SELECTED')
          FROM minute_ma_selection_snapshot WHERE selection_batch_id=%s""",(args.batch_id,))
        total,pending,selected=cur.fetchone()
        if total!=9600 or pending!=2400: raise RuntimeError((total,pending,selected))
        if args.approve:
            cur.execute("UPDATE minute_ma_selection_batch SET status='APPROVED' WHERE selection_batch_id=%s AND status='DRAFT'",(args.batch_id,))
        if args.apply_live_operation:
            cur.execute("""WITH selected AS (
                SELECT minute_path_id FROM minute_ma_selection_snapshot
                 WHERE selection_batch_id=%s AND decision_status='SELECTED'
              ) UPDATE minute_ma_operation o SET effective_to=CURRENT_TIMESTAMP
                 FROM selected s WHERE o.minute_path_id=s.minute_path_id AND o.effective_to IS NULL
                   AND o.operation_status='PAPER'""",(args.batch_id,))
            cur.execute("""INSERT INTO minute_ma_operation(minute_path_id,operation_status,allocated_amount,
              capital_epoch_no,effective_from,change_reason,audit_reference)
              SELECT minute_path_id,'LIVE',20000,1,CURRENT_TIMESTAMP,'MANUAL',%s
                FROM minute_ma_selection_snapshot WHERE selection_batch_id=%s AND decision_status='SELECTED'
              RETURNING operation_id,minute_path_id""",(args.batch_id,args.batch_id))
            operations=cur.fetchall()
            cur.executemany("""INSERT INTO minute_ma_compound_capital(
              minute_path_id,capital_epoch_no,source_operation_id,epoch_initial_capital,
              strategy_compound_capital,cumulative_net_realized_pnl)
              VALUES (%s,1,%s,20000,20000,0)""",[(path_id,op_id) for op_id,path_id in operations])
        conn.commit()
        print(json.dumps({"batch_id":args.batch_id,"snapshots":total,"pending":pending,
                          "selected":selected,"approved":args.approve,
                          "live_operation_applied":args.apply_live_operation}))
    finally: pool.close()
    return 0

if __name__=='__main__': raise SystemExit(main())
