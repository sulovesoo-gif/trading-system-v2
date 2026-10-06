import unittest
import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock,patch,AsyncMock

from src.collector.raw.domestic_stock.holiday_calendar_collector import HolidayCalendarCollector
from src.service.kis_trading_calendar import KisTradingCalendar
from src.first_rise_breakout.trading_day import FirstRiseTradingDay
from src.first_rise_breakout.runtime import FirstRiseBreakoutRuntime
from src.first_rise_breakout.strategy import FirstRiseBreakoutStrategy
from src.first_rise_breakout.j_live_runtime import JLiveRuntime
from src.first_rise_breakout.models import ResearchState
from test.test_first_rise_j_runtime import Repository,Source
from test.test_first_rise_j import CONFIG,state,at,bar
from test import test_first_rise_j_wiring_e2e as pg_fixture


class TradingDayTests(unittest.TestCase):
    def calendar(self,opened):
        self.client=Mock()
        if isinstance(opened,Exception):self.client.get.side_effect=opened
        else:
            self.client.get.side_effect=lambda **kw:{'output':[{'bass_dt':kw['params']['BASS_DT'],'opnd_yn':opened}]}
        return FirstRiseTradingDay(KisTradingCalendar(HolidayCalendarCollector(self.client)))

    def runtime(self,day):
        self.jrepo=Repository();self.source=Source();candidate=state().tracking
        self.repo=Mock(spec=['record_condition_hits','record_candidate','j_market_repository','apply'])
        self.repo.j_market_repository.return_value=self.jrepo
        self.repo.record_candidate.return_value=(candidate,True)
        self.search=Mock();self.search.resolve_seq.return_value='7'
        self.search.candidates.return_value=[SimpleNamespace(stock_code=candidate.stock_code,stock_name='fixture',raw_payload={})]
        self.source.bars=[bar(9,0,1030),bar(9,1,1025,1000),bar(9,9,1031,1025)]
        return FirstRiseBreakoutRuntime(repository=self.repo,strategy=FirstRiseBreakoutStrategy(),
            condition_search=self.search,minute_source=self.source,config=CONFIG,
            now_provider=lambda:at(9,1),trading_day=day)

    def test_open_day_normal_signal_and_once_per_date(self):
        day=self.calendar('Y');r=self.runtime(day)
        r.scan_once(at=at(9,1));r.refresh_completed_bars(at=at(9,10));r.expire_once(at=at(9,10))
        self.assertEqual(len([s for s in self.jrepo.events if s.market_entry]),1)
        self.assertEqual(self.client.get.call_count,1)
        self.assertEqual(self.client.get.call_args.kwargs['tr_id'],'CTCA0903R')
        day(at(9,1)+timedelta(days=1));self.assertEqual(self.client.get.call_count,2)

    def test_holiday_no_search_minute_signal_or_state_mutation(self):
        day=self.calendar('N');r=self.runtime(day)
        existing=replace(state().tracking,state=ResearchState.PAPER_ENTERED)
        r._states[existing.stock_code]=existing
        self.source.completed_bars_from_open=Mock(side_effect=AssertionError('holiday minute GET'))
        for minute in (1,10,20):
            self.assertEqual(r.scan_once(at=at(9,minute)),0)
            self.assertEqual(r.refresh_completed_bars(at=at(9,minute)),0)
        self.assertEqual(r.expire_once(at=at(15,1)),0)
        self.search.candidates.assert_not_called();self.search.resolve_seq.assert_not_called()
        self.repo.record_candidate.assert_not_called();self.repo.apply.assert_not_called()
        self.source.completed_bars_from_open.assert_not_called()
        self.assertEqual(self.jrepo.events,[]);self.assertIs(r._states[existing.stock_code],existing)
        self.assertEqual(self.client.get.call_count,1)

    def test_failure_blocks_only_cycle_then_recovers_same_day(self):
        day=self.calendar(RuntimeError('mock calendar unavailable'));r=self.runtime(day)
        with self.assertLogs('src.first_rise_breakout.trading_day',level='ERROR'):
            self.assertEqual(r.scan_once(at=at(9,1)),0)
        self.assertEqual(self.client.get.call_count,1)
        self.assertIsNone(day.day)
        self.search.candidates.assert_not_called();self.assertEqual(self.jrepo.events,[])
        self.client.get.side_effect=lambda **kw:{'output':[{'bass_dt':kw['params']['BASS_DT'],'opnd_yn':'Y'}]}
        self.assertEqual(r.scan_once(at=at(9,2)),1)
        self.assertTrue(day(at(9,3)))
        self.assertEqual(self.client.get.call_count,2)

    def test_live_no_new_buy_planning_recovery_unchanged(self):
        for opened in ('N',RuntimeError('calendar down')):
            day=self.calendar(opened)
            context=SimpleNamespace(load=Mock(),config=CONFIG)
            planner=Mock();store=Mock();store.discover_ready_request_keys.return_value=[]
            recovery=Mock();cost=Mock()
            r=JLiveRuntime(context=context,planner=planner,submit_store=store,submitter=Mock(),
                recovery=recovery,price_lookup=Mock(),cash_lookup=Mock(),cost_finalizer=cost,trading_day=day)
            r.cycle(at=at(9,10));r.cycle(at=at(9,11))
            planner.entry_signals.assert_not_called();planner.plan_buy.assert_not_called()
            self.assertEqual(recovery.poll.call_count,2)
            self.assertEqual(self.client.get.call_count,2 if isinstance(opened,Exception) else 1)

    def test_holiday_run_loop_skips_restore_and_all_work(self):
        r=self.runtime(self.calendar('N'))
        for name in ('restore','scan_once','refresh_completed_bars','expire_once','_load_daily_config'):
            setattr(r,name,Mock(side_effect=AssertionError('holiday work forbidden')))
        with patch('src.first_rise_breakout.runtime.asyncio.sleep',new=AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):asyncio.run(r.run_forever())
        self.assertEqual(self.client.get.call_count,1)

    def test_live_open_day_planning_continues(self):
        day=self.calendar('Y');context=SimpleNamespace(load=Mock(),config=CONFIG)
        planner=Mock();planner.entry_signals.return_value=['signal']
        store=Mock();store.discover_ready_request_keys.return_value=[]
        r=JLiveRuntime(context=context,planner=planner,submit_store=store,submitter=Mock(),
            recovery=Mock(),price_lookup=Mock(),cash_lookup=Mock(),cost_finalizer=Mock(),trading_day=day)
        r.cycle(at=at(9,10))
        planner.plan_buy.assert_called_once()
        self.assertEqual(self.client.get.call_count,1)

    def test_live_calendar_error_recovers_next_cycle_without_restart(self):
        day=self.calendar(PermissionError('mock token permission'))
        planner=Mock();planner.entry_signals.return_value=[]
        store=Mock();store.discover_ready_request_keys.return_value=[]
        recovery=Mock()
        r=JLiveRuntime(context=SimpleNamespace(load=Mock(),config=CONFIG),planner=planner,
            submit_store=store,submitter=Mock(),recovery=recovery,price_lookup=Mock(),
            cash_lookup=Mock(),cost_finalizer=Mock(),trading_day=day)
        with self.assertLogs('src.first_rise_breakout.trading_day',level='ERROR'):
            r.cycle(at=at(9,10))
        planner.entry_signals.assert_not_called()
        self.client.get.side_effect=lambda **kw:{'output':[{'bass_dt':kw['params']['BASS_DT'],'opnd_yn':'Y'}]}
        r.cycle(at=at(9,11));r.cycle(at=at(9,12))
        self.assertEqual(planner.entry_signals.call_count,2)
        self.assertEqual(recovery.poll.call_count,3)
        self.assertEqual(self.client.get.call_count,2)


class TradingDayPostgresTests(pg_fixture.WiringE2E):
    def snapshot(self):
        return {name:self.query('SELECT count(*) FROM '+name)[0][0] for name in (
            'first_rise_j_candidate','first_rise_j_market_signal','first_rise_j_shadow_trade',
            'first_rise_j_live_intent','live_order_request','live_broker_order')}

    def test_closed_day_market_and_actual_rows_unchanged(self):
        day=FirstRiseTradingDay(SimpleNamespace(open_dates=lambda *_:[]))
        before=self.snapshot()
        source=Mock();search=Mock()
        runtime=FirstRiseBreakoutRuntime(repository=SimpleNamespace(),strategy=FirstRiseBreakoutStrategy(),
            condition_search=search,minute_source=source,config=self.config,
            now_provider=lambda:self.now,trading_day=day)
        runtime.j_market=self.market
        runtime.scan_once(at=self.now);runtime.refresh_completed_bars(at=self.now)
        self.live.trading_day=day;self.cycle()
        self.assertEqual(self.snapshot(),before)
        source.completed_bars_from_open.assert_not_called();search.candidates.assert_not_called()
        self.assertEqual(self.posts,[])

    def test_closed_day_existing_pending_fill_recovery_kept(self):
        self.cycle();self.fill('1',qty=2)
        self.live.trading_day=FirstRiseTradingDay(SimpleNamespace(open_dates=lambda *_:[]))
        self.cycle()
        self.assertEqual(self.query('SELECT buy_quantity FROM first_rise_j_live_cost'),[(2,)])
        self.assertEqual(len(self.posts),1)
        self.assertEqual(self.query('SELECT status FROM live_broker_order'),[('PARTIALLY_FILLED',)])

    def test_open_day_existing_actual_path_kept(self):
        self.live.trading_day=FirstRiseTradingDay(SimpleNamespace(open_dates=lambda start,end:[start]))
        self.cycle();self.assertEqual(len(self.posts),1)
