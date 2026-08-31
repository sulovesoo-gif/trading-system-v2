"""Durable finalized-bar trigger for Minute V1 PAPER and LIVE consumers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable,Sequence


@dataclass(frozen=True)
class DispatchWatermark:
    finalized_at: datetime
    bar_time: datetime
    stock_code: str


class MinuteV1RealtimeDispatchRepository:
    CONSUMERS=("V1_PAPER","V1_LIVE")

    def __init__(self,pool):self.pool=pool

    def latest_eligible(self,*,consumer_code:str)->DispatchWatermark|None:
        sql="""SELECT b.finalized_at,b.bar_time,b.stock_code
          FROM flow_realtime_minute_bar b
          JOIN minute_ma_realtime_dispatch_cursor c ON c.consumer_code=%s
          WHERE b.stock_code IN ('005930','000660') AND b.quality_status<>'INCOMPLETE'
            AND NOT b.source_gap_flag AND NOT b.reconnect_flag
            AND NOT b.event_time_regression_flag AND NOT b.ordering_invariant_failure
            AND NOT b.accumulated_volume_regression
            AND (c.last_finalized_at IS NULL OR
                 (b.finalized_at,b.bar_time,b.stock_code)>
                 (c.last_finalized_at,c.last_bar_time,c.last_stock_code))
          ORDER BY b.finalized_at DESC,b.bar_time DESC,b.stock_code DESC LIMIT 1"""
        with self.pool.connection() as connection,connection.cursor() as cursor:
            cursor.execute(sql,(consumer_code,));row=cursor.fetchone()
        return None if row is None else DispatchWatermark(row[0],row[1],str(row[2]))

    def cursor_is_empty(self,*,consumer_code:str)->bool:
        with self.pool.connection() as connection,connection.cursor() as cursor:
            cursor.execute("SELECT last_finalized_at IS NULL FROM minute_ma_realtime_dispatch_cursor WHERE consumer_code=%s",
                           (consumer_code,));row=cursor.fetchone()
        return row is not None and bool(row[0])

    def bootstrap_no_replay(self,*,consumer_code:str,watermark:DispatchWatermark)->None:
        runtime="MINUTE_MA_V1_PAPER" if consumer_code=="V1_PAPER" else "MINUTE_MA_V1_LIVE_NOSEND"
        with self.pool.connection() as connection,connection.cursor() as cursor:
            cursor.execute("""UPDATE minute_ma_realtime_dispatch_cursor SET last_finalized_at=%s,
              last_bar_time=%s,last_stock_code=%s,last_success_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
              WHERE consumer_code=%s AND last_finalized_at IS NULL""",
              (watermark.finalized_at,watermark.bar_time,watermark.stock_code,consumer_code))
            cursor.execute("""INSERT INTO minute_ma_policy_runtime_cursor(
              runtime_name,policy_version,signal_code,last_source_bar_time)
              SELECT %s,'V1.0',stock_code,max(bar_time) FROM flow_realtime_minute_bar
              WHERE stock_code IN ('005930','000660') AND finalized_at<=%s GROUP BY stock_code
              ON CONFLICT(runtime_name,policy_version,signal_code) DO UPDATE SET
                last_source_bar_time=GREATEST(minute_ma_policy_runtime_cursor.last_source_bar_time,
                                               EXCLUDED.last_source_bar_time),
                updated_at=CURRENT_TIMESTAMP""",(runtime,watermark.finalized_at))
            connection.commit()

    def advance(self,*,consumer_code:str,watermark:DispatchWatermark)->None:
        with self.pool.connection() as connection,connection.cursor() as cursor:
            cursor.execute("""UPDATE minute_ma_realtime_dispatch_cursor SET last_finalized_at=%s,
              last_bar_time=%s,last_stock_code=%s,last_success_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
              WHERE consumer_code=%s AND (last_finalized_at,last_bar_time,last_stock_code)<(%s,%s,%s)""",
              (watermark.finalized_at,watermark.bar_time,watermark.stock_code,consumer_code,
               watermark.finalized_at,watermark.bar_time,watermark.stock_code));connection.commit()


class MinuteV1RealtimeDispatcher:
    def __init__(self,repository,*,commands:dict[str,Sequence[str]],runner:Callable[[Sequence[str]],int]):
        self.repository=repository;self.commands=commands;self.runner=runner

    def poll_once(self)->dict[str,str]:
        results={}
        for consumer in self.repository.CONSUMERS:
            watermark=self.repository.latest_eligible(consumer_code=consumer)
            if watermark is None:continue
            if self.repository.cursor_is_empty(consumer_code=consumer):
                self.repository.bootstrap_no_replay(consumer_code=consumer,watermark=watermark)
                results[consumer]="BOOTSTRAPPED_NO_REPLAY";continue
            return_code=self.runner(self.commands[consumer])
            if return_code==0:
                self.repository.advance(consumer_code=consumer,watermark=watermark)
                results[consumer]="DISPATCHED"
            else:
                results[consumer]=f"FAILED_{return_code}"
        return results
