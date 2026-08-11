from datetime import datetime,timedelta
from decimal import Decimal
import unittest

from src.analysis.feature.sma_feature import MinuteBar
from src.analysis.video_strategy import VideoFeatureEngine,VideoParameters,VideoEvent,execution_direction,measure_event

D=Decimal


def bar(at,o,h,l,c): return MinuteBar(at,D(o),D(h),D(l),D(c))


class VideoStrategyFeatureTest(unittest.TestCase):
    def test_body_wick_and_zero_body_are_safe(self):
        start=datetime(2026,8,3,9)
        rows=[(bar(start+timedelta(minutes=i),"100","105","95","100"),D(10)) for i in range(20)]
        f=VideoFeatureEngine(VideoParameters()).build(rows)[0][-1]
        self.assertEqual(f.body_size,0); self.assertIsNone(f.upper_wick_body_ratio); self.assertIsNone(f.range_body_ratio)

    def test_sma20_and_slope(self):
        start=datetime(2026,8,3,9)
        rows=[(bar(start+timedelta(minutes=i),str(i+1),str(i+1),str(i+1),str(i+1)),D(10)) for i in range(23)]
        features,_=VideoFeatureEngine(VideoParameters(sma_slope_window=3,sma_slope_min_ratio=D("0"))).build(rows)
        self.assertEqual(features[19].sma20,D("10.5")); self.assertEqual(features[-1].sma20_slope,D(3)); self.assertEqual(features[-1].sma20_direction,"UP")

    def test_body_above_below_sma(self):
        start=datetime(2026,8,3,9)
        rows=[(bar(start+timedelta(minutes=i),"100","101","99","100"),D(10)) for i in range(19)]
        rows.append((bar(start+timedelta(minutes=19),"99","102","98","101"),D(10)))
        f=VideoFeatureEngine(VideoParameters()).build(rows)[0][-1]
        self.assertIsNotNone(f.body_above_ratio); self.assertAlmostEqual(float(f.body_above_ratio+f.body_below_ratio),1)

    def test_previous_body_expansion(self):
        start=datetime(2026,8,3,9)
        rows=[(bar(start,"100","101","99","101"),D(10)),(bar(start+timedelta(minutes=1),"100","103","99","102"),D(10))]
        self.assertTrue(VideoFeatureEngine(VideoParameters(body_expansion_ratio=D("1.5"))).build(rows)[0][-1].body_expansion)

    def test_volume_average_ratio_spike_drop(self):
        start=datetime(2026,8,3,9); volumes=[10]*20+[30,5]
        rows=[(bar(start+timedelta(minutes=i),"100","101","99","101"),D(v)) for i,v in enumerate(volumes)]
        features,events=VideoFeatureEngine(VideoParameters(volume_avg_window=20,volume_spike_ratio=D(2),volume_drop_ratio=D("0.6"))).build(rows)
        self.assertGreater(features[-2].volume_ratio,2); self.assertTrue({"VOLUME_SPIKE","VOLUME_DROP"}<={e.event_type for e in events})

    def test_pivot_is_not_visible_before_confirmed_time(self):
        start=datetime(2026,8,3,9); highs=[1,2,5,2,1]
        rows=[(bar(start+timedelta(minutes=i),"0",str(h),"0","1"),D(1)) for i,h in enumerate(highs)]
        features,events=VideoFeatureEngine(VideoParameters()).build(rows)
        pivot=next(e for e in events if e.event_type=="STRUCTURE_HIGH")
        self.assertEqual(pivot.detail["pivot_time"],start+timedelta(minutes=2)); self.assertEqual(pivot.at,start+timedelta(minutes=4))
        self.assertTrue(all(f.pivot_high is None for f in features[:4]))

    def test_gap_resets_sma_history(self):
        start=datetime(2026,8,3,9)
        rows=[(bar(start+timedelta(minutes=i),"1","1","1","1"),D(1)) for i in range(20)]
        rows += [(bar(datetime(2026,8,3,15,40)+timedelta(minutes=i),"2","2","2","2"),D(1)) for i in range(19)]
        self.assertIsNone(VideoFeatureEngine(VideoParameters()).build(rows)[0][-1].sma20)

    def test_battle_candle(self):
        start=datetime(2026,8,3,9)
        rows=[(bar(start+timedelta(minutes=i),"100","101","99","101"),D(10)) for i in range(20)]
        rows.append((bar(start+timedelta(minutes=20),"100","110","90","101"),D(100)))
        types={e.event_type for e in VideoFeatureEngine(VideoParameters(volume_avg_window=20)).build(rows)[1]}
        self.assertIn("BATTLE_CANDLE",types); self.assertIn("REVERSAL_WARNING",types)

    def test_zigzag_unknown_does_not_guess_future_pivot(self):
        start=datetime(2026,8,3,9); rows=[(bar(start+timedelta(minutes=i),"1","3","0","2"),D(1)) for i in range(30)]
        features,events=VideoFeatureEngine(VideoParameters(pivot_method="PIVOT_ZIGZAG")).build(rows)
        self.assertFalse(any(e.event_type.startswith("STRUCTURE_") for e in events)); self.assertTrue(all(f.pivot_high is None for f in features))


class VideoProjectionTest(unittest.TestCase):
    def setUp(self):
        self.at=datetime(2026,8,3,9); self.f=VideoFeatureEngine(VideoParameters())._placeholder(bar(self.at,"100","101","99","100"),D(10))

    def test_source_execution_mapping(self):
        self.assertEqual(execution_direction("LONG","0193T0"),"LONG"); self.assertEqual(execution_direction("SHORT","000660"),"VIRTUAL_SHORT"); self.assertEqual(execution_direction("SHORT","0197X0"),"LONG")

    def test_exact_timestamp_missing(self):
        event=VideoEvent(self.at,"LONG_ENTRY","LONG",D(100),self.f)
        self.assertEqual(measure_event(event,{},"0193T0")["data_status"],"TRADE_PRICE_MISSING")

    def test_forward_returns_and_high_low_mfe_mae(self):
        event=VideoEvent(self.at,"LONG_ENTRY","LONG",D(100),self.f)
        target={self.at:bar(self.at,"10","10","10","10"),self.at+timedelta(minutes=1):bar(self.at+timedelta(minutes=1),"10","12","8","11")}
        measured=measure_event(event,target,"0193T0")
        self.assertEqual(measured["return_1m"],D("0.1")); self.assertEqual(measured["mfe"],D("0.2")); self.assertEqual(measured["mae"],D("-0.2"))

    def test_short_projection_is_directional(self):
        event=VideoEvent(self.at,"SHORT_ENTRY","SHORT",D(100),self.f)
        target={self.at:bar(self.at,"10","10","10","10"),self.at+timedelta(minutes=1):bar(self.at+timedelta(minutes=1),"9","9","9","9")}
        self.assertEqual(measure_event(event,target,"000660")["return_1m"],D("0.1"))

    def test_parameter_snapshot_is_reproducible(self):
        snapshot=VideoParameters().snapshot()
        for key in ("strategy_family","strategy_version","signal_source_stock_code","execution_stock_codes","timeframe","sma_length","pivot_method","capital_policy","cost_policy_version"):
            self.assertIn(key,snapshot)


if __name__=="__main__":unittest.main()
