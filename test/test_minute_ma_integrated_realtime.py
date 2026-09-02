from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path

from src.minute_ma.integrated_realtime_contracts import (
    INTEGRATED_EXECUTION_FIELDS, IntegratedRealtimeContractError,
    integrated_source_datetime, split_integrated_execution_frame,
)
from src.flow_raw.realtime_minute import ExecutionTick, build_realtime_minute_bars


def record(code: str, hour: str) -> list[str]:
    values=["0"]*len(INTEGRATED_EXECUTION_FIELDS)
    fields={name:index for index,name in enumerate(INTEGRATED_EXECUTION_FIELDS)}
    values[fields["MKSC_SHRN_ISCD"]]=code
    values[fields["STCK_CNTG_HOUR"]]=hour
    values[fields["STCK_PRPR"]]="72000"
    values[fields["CNTG_VOL"]]="10"
    values[fields["ACML_VOL"]]="1000"
    values[fields["BSOP_DATE"]]="20260902"
    return values


class MinuteMaIntegratedRealtimeTest(unittest.TestCase):
    def test_one_message_preserves_multi_record_order(self):
        payload=record("005930","090001")+record("000660","090002")
        events=split_integrated_execution_frame("0|H0UNCNT0|2|"+"^".join(payload))
        self.assertEqual([event.event_index for event in events],[0,1])
        self.assertEqual([event.values["MKSC_SHRN_ISCD"] for event in events],["005930","000660"])
        self.assertEqual(integrated_source_datetime(events[0],received_at=datetime(2026,9,2,9,0,2)),
                         datetime(2026,9,2,9,0,1))

    def test_wrong_width_fails_closed(self):
        with self.assertRaises(IntegratedRealtimeContractError):
            split_integrated_execution_frame("0|H0UNCNT0|1|005930^090001")

    def test_integrated_ticks_build_a_completed_minute(self):
        connected=datetime(2026,9,2,8,59,50)
        def tick(at,price,accum,sequence):
            return ExecutionTick('005930',at,connected,sequence,0,at,price,1,accum,
                                 'connection',False,False,False,False)
        ticks=(
            tick(datetime(2026,9,2,9,0,1),100,100,1),
            tick(datetime(2026,9,2,9,0,59),101,110,2),
            tick(datetime(2026,9,2,9,1,1),102,120,3),
            tick(datetime(2026,9,2,9,1,59),99,130,4),
            tick(datetime(2026,9,2,9,2,0),103,140,5),
        )
        bars=build_realtime_minute_bars(ticks,now=datetime(2026,9,2,9,2,1),grace_ms=2000)
        bar=next(item for item in bars if item.bar_time==datetime(2026,9,2,9,1))
        self.assertEqual((bar.open_price,bar.high_price,bar.low_price,bar.close_price,bar.volume),
                         (102,102,99,99,20))
        self.assertEqual((bar.finalize_reason,bar.quality_status),('NEXT_MINUTE_EVENT','COMPLETE'))

    def test_flow_contract_files_are_not_rewritten_for_integrated(self):
        flow=(Path('src/flow_raw/collector.py').read_text(encoding='utf-8')+
              Path('src/flow_raw/contracts.py').read_text(encoding='utf-8'))
        self.assertIn('H0STCNT0',flow)
        self.assertIn('H0STPGM0',flow)
        self.assertIn('H0STASP0',flow)
        self.assertNotIn('H0UNCNT0',flow)

    def test_additive_tables_are_venue_isolated(self):
        migration=Path('database/migrations/20260902_minute_ma_integrated_realtime_additive.sql').read_text(encoding='utf-8')
        self.assertIn("CHECK (trading_venue='INTEGRATED')",migration)
        self.assertIn("CHECK (tr_id='H0UNCNT0')",migration)
        self.assertNotIn('ALTER TABLE flow_realtime_minute_bar',migration)


if __name__=='__main__':
    unittest.main()
