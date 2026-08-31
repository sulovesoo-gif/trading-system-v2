from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from src.flow_raw.realtime_minute import ExecutionTick, build_realtime_minute_bars, compare_rest


BASE = datetime(2026, 8, 31, 9, 0)


def tick(second, price, accumulated, *, execution=1, sequence=1, index=0,
         connection="c1", connected=BASE-timedelta(seconds=1), received_ms=100,
         duplicate=False, reconnect=False, gap=False, regression=False, stock="005930"):
    event_time = BASE + timedelta(seconds=second)
    return ExecutionTick(stock,event_time,connected,sequence,index,
                         event_time+timedelta(milliseconds=received_ms),price,execution,
                         accumulated,connection,reconnect,gap,regression,duplicate)


class RealtimeMinuteBuilderTest(unittest.TestCase):
    def build(self, items, now=BASE+timedelta(minutes=3)):
        return build_realtime_minute_bars(items,now=now,grace_ms=2000)

    def test_01_official_order_same_second_uses_connection_sequence_index(self):
        a=tick(1,100,10,sequence=2,index=0);b=tick(1,90,9,sequence=1,index=1)
        bar=self.build([a,b,tick(61,110,20,sequence=3)])[0]
        self.assertEqual((bar.open_price,bar.close_price),(90,100))

    def test_02_ohlc_uses_first_max_min_last(self):
        bar=self.build([tick(1,100,10),tick(2,110,11,sequence=2),tick(3,95,12,sequence=3),tick(61,120,20,sequence=4)])[0]
        self.assertEqual((bar.open_price,bar.high_price,bar.low_price,bar.close_price),(100,110,95,95))

    def test_03_next_minute_event_finalizes_previous(self):
        bar=self.build([tick(1,100,10),tick(60,101,11,sequence=2)])[0]
        self.assertEqual(bar.finalize_reason,"NEXT_MINUTE_EVENT")

    def test_04_unclosed_current_minute_is_not_emitted(self):
        self.assertEqual(self.build([tick(1,100,10)],now=BASE+timedelta(seconds=61)),())

    def test_05_grace_watermark_finalizes_without_fake_bar(self):
        bars=self.build([tick(1,100,10)],now=BASE+timedelta(seconds=63))
        self.assertEqual((len(bars),bars[0].finalize_reason),(1,"GRACE_WATERMARK"))

    def test_06_no_trade_minute_is_not_invented(self):
        bars=self.build([tick(1,100,10),tick(121,120,30,sequence=2)])
        self.assertNotIn(BASE+timedelta(minutes=1),{bar.bar_time for bar in bars})

    def test_07_duplicate_is_excluded(self):
        bars=self.build([tick(1,100,10),tick(2,999,11,sequence=2,duplicate=True),tick(61,101,12,sequence=3)])
        self.assertEqual((bars[0].high_price,bars[0].duplicate_excluded_count),(100,1))

    def test_08_message_count_deduplicates_multi_record_frame(self):
        bars=self.build([tick(1,100,10,sequence=1,index=0),tick(2,101,11,sequence=1,index=1),tick(61,102,12,sequence=2)])
        self.assertEqual(bars[0].message_count,1)

    def test_09_volume_uses_accumulated_delta(self):
        bars=self.build([tick(1,100,100),tick(59,101,120,sequence=2),tick(61,102,150,sequence=3)])
        self.assertEqual(bars[1].volume,30)

    def test_10_first_minute_volume_is_null_and_incomplete(self):
        bar=self.build([tick(1,100,100),tick(61,101,120,sequence=2)])[0]
        self.assertIsNone(bar.volume);self.assertEqual(bar.quality_status,"INCOMPLETE")

    def test_11_accumulated_regression_fails_closed(self):
        bars=self.build([tick(1,100,100),tick(61,101,90,sequence=2),tick(121,102,110,sequence=3)])
        self.assertIsNone(bars[1].volume);self.assertTrue(bars[1].accumulated_volume_regression)

    def test_12_reconnect_and_gap_are_quality_evidence(self):
        bars=self.build([tick(1,100,100),tick(61,101,110,sequence=2,reconnect=True,gap=True),tick(121,102,120,sequence=3)])
        self.assertTrue(bars[1].reconnect_flag);self.assertEqual(bars[1].quality_status,"INCOMPLETE")

    def test_13_execution_volume_sum_is_cross_check_only(self):
        bar=self.build([tick(1,100,10,execution=3),tick(2,101,11,execution=4,sequence=2),tick(61,102,12,sequence=3)])[0]
        self.assertEqual(bar.execution_volume_sum,7);self.assertIsNone(bar.volume)

    def test_14_rest_comparison_does_not_mutate_bar(self):
        bar=self.build([tick(1,100,10),tick(61,101,20,sequence=2)])[0]
        status,mismatches=compare_rest(bar,SimpleNamespace(open_price=100,high_price=100,low_price=100,close_price=99,volume=0))
        self.assertEqual(status,"MISMATCH");self.assertIn("close",mismatches);self.assertEqual(bar.close_price,100)

    def test_15_repeat_build_is_deterministic(self):
        items=[tick(1,100,10),tick(61,101,20,sequence=2)]
        self.assertEqual(self.build(items),self.build(list(reversed(items))))

    def test_16_execution_product_next_minute_open_is_real_first_trade(self):
        bars=self.build([tick(60,5010,100,stock="0193W0",sequence=2,index=1),
                         tick(60,5000,99,stock="0193W0",sequence=2,index=0),
                         tick(120,5020,110,stock="0193W0",sequence=3)])
        minute_one=next(bar for bar in bars if bar.bar_time==BASE+timedelta(minutes=1))
        self.assertEqual(minute_one.open_price,5000)

    def test_17_repository_contract_is_insert_only_for_restart_idempotency(self):
        from pathlib import Path
        source=Path("src/flow_raw/realtime_minute_repository.py").read_text(encoding="utf-8")
        self.assertIn("ON CONFLICT(bar_time,stock_code,trading_venue) DO NOTHING",source)


if __name__ == "__main__":
    unittest.main()
