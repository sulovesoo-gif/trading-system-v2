from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
import unittest

from src.minute_ma.contracts import MinuteBar
from src.minute_ma.engine import PreparedMaPoint, SignalEvent, SignalType
from src.minute_ma.v1_historical import MinuteMaV1HistoricalReplay
from src.minute_ma.v1_policy import LONG_POLICY, SHORT_POLICY
from src.service.minute_ma_dashboard_service import (
    _period_window, _positive_period_frequency, _virtual_metrics,
)


def bar(at, value):
    return MinuteBar(at, value, value, value, value, 1)


class Events:
    def __init__(self, events): self.events = events
    def evaluate_prepared(self, **kwargs): return tuple(self.events)


class V1HistoricalTest(unittest.TestCase):
    def _path(self, direction, policy):
        return SimpleNamespace(direction=direction, operation_policy=policy)

    def test_long_entry_holds_overnight_until_normal_exit(self):
        entry_at=datetime(2026,7,1,14,0); exit_at=datetime(2026,7,2,10,0)
        events=[
            SignalEvent(1,"p",SignalType.ENTRY,entry_at,datetime(2026,7,1,14,1,1),"a"*64,True,{},{}),
            SignalEvent(1,"p",SignalType.EXIT,exit_at,datetime(2026,7,2,10,1,1),"b"*64,True,{},{}),
        ]
        points=(PreparedMaPoint(entry_at,{},None,100),PreparedMaPoint(exit_at,{},None,101))
        executions={datetime(2026,7,1,14,1):bar(datetime(2026,7,1,14,1),100),
                    datetime(2026,7,2,10,1):bar(datetime(2026,7,2,10,1),110)}
        result=MinuteMaV1HistoricalReplay(engine=Events(events)).replay(
            path=self._path("LONG",LONG_POLICY),prepared_points=points,
            execution_bars=executions,underlying_bars=executions,
            evaluation_from=date(2026,7,1),evaluation_to=date(2026,7,2))
        self.assertEqual(1,len(result));self.assertEqual("NORMAL_EXIT",result[0].exit_reason)
        self.assertEqual(Decimal("9.80"),result[0].net_return_pct)

    def test_short_stop_uses_immutable_anchor_and_next_open(self):
        entry_at=datetime(2026,7,1,9,0); stop_at=datetime(2026,7,2,9,0)
        event=SignalEvent(1,"p",SignalType.ENTRY,entry_at,datetime(2026,7,1,9,1,1),"c"*64,True,{},{})
        points=(PreparedMaPoint(entry_at,{},None,100),PreparedMaPoint(stop_at,{},None,102))
        executions={datetime(2026,7,1,9,1):bar(datetime(2026,7,1,9,1),100),
                    datetime(2026,7,2,9,1):bar(datetime(2026,7,2,9,1),103)}
        result=MinuteMaV1HistoricalReplay(engine=Events([event])).replay(
            path=self._path("SHORT",SHORT_POLICY),prepared_points=points,
            execution_bars=executions,underlying_bars={
                datetime(2026,7,1,9,1):bar(datetime(2026,7,1,9,1),100)},
            evaluation_from=date(2026,7,1),evaluation_to=date(2026,7,2))
        self.assertEqual("STOP_EXIT",result[0].exit_reason)
        self.assertEqual(Decimal("100"),result[0].underlying_entry_reference_price)
        self.assertEqual(stop_at,result[0].stop_trigger_time)

    def test_period_metrics_use_exit_date_and_one_million_virtual_capital(self):
        rows=[
            (1,datetime(2026,7,31,15,0),Decimal("10"),"NORMAL_EXIT","1","HISTORICAL_REPLAY"),
            (1,datetime(2026,8,3,10,0),Decimal("-5"),"STOP_EXIT","2","PAPER_FORWARD"),
        ]
        _,start,_=_period_window(date(2026,8,15),"MONTHLY")
        metric=_virtual_metrics(rows,path_ids=[1],start=start)[1]
        self.assertEqual(Decimal("1100000.0"),metric["period_start_capital"])
        self.assertEqual(Decimal("-5.00"),metric["period_compound_return_pct"])
        self.assertEqual(1,metric["period_closed_trade_count"])
        self.assertEqual(1,metric["period_stop_count"])

    def test_positive_period_frequency_excludes_empty_periods(self):
        rows=[
            (1,datetime(2026,7,31,15,0),Decimal("10")),
            (1,datetime(2026,8,3,10,0),Decimal("-5")),
            (1,datetime(2026,8,3,11,0),Decimal("6")),
            (1,datetime(2026,8,10,10,0),Decimal("-1")),
        ]
        metric=_positive_period_frequency(rows)
        self.assertEqual((2,3),(metric["positive_day_count"],metric["evaluable_day_count"]))
        self.assertEqual((2,3),(metric["positive_week_count"],metric["evaluable_week_count"]))
        self.assertEqual((1,2),(metric["positive_month_count"],metric["evaluable_month_count"]))
        self.assertNotIn(date(2026,8,4),rows)

    def test_research_schema_is_provenance_isolated(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        sql=(root/"database/migrations/20260830_minute_ma_dashboard_research_tracking_additive.sql").read_text(encoding="utf-8")
        self.assertIn("minute_ma_policy_historical_trade",sql)
        self.assertIn("'HISTORICAL_REPLAY'",sql)
        self.assertNotIn("INSERT INTO minute_ma_policy_paper_trade",sql)
        self.assertNotIn("CHECK (basis_capital>0)",sql)
        compatibility=(root/"database/migrations/20260830_minute_ma_historical_basis_capital_compatibility.sql").read_text(encoding="utf-8")
        self.assertIn("DROP CONSTRAINT IF EXISTS minute_ma_policy_historical_trade_basis_capital_check",compatibility)
        page=(root/"reports/multi-ma/minute-ma.html").read_text(encoding="utf-8")
        self.assertIn("기준일",page)
        self.assertIn("state={scope:'V1_LIVE'",page)
        self.assertIn("실제 기간수익률",page)
        self.assertIn("양수기간",page);self.assertIn("Rank(최근5)",page)
        self.assertIn("LIVE_ACTUAL",page)
        self.assertNotIn('class="strategy-card"',page)
        self.assertIn('class="strategy-core-row"',page)
        self.assertIn('class="strategy-detail-row"',page)
        self.assertIn('data-strategy-group=',page)
        self.assertIn('#v1Section table{min-width:2100px;table-layout:fixed}',page)
        runner=(root/"scripts/research/run_minute_ma_v1_historical_replay.py").read_text(encoding="utf-8")
        self.assertIn("'KRX_1MIN_COMPLETED_V1_POLICY',%s,'RUNNING'",runner)
        self.assertNotIn("minute_ma_policy_paper_trade",runner)
        self.assertEqual(1,runner.count("write_connection.commit()"))
        self.assertIn('"mode": "EXISTING"',runner)
