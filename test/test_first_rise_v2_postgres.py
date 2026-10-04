"""TEMP PostgreSQL + fake broker V2 capacity integration."""
from decimal import Decimal as D
from psycopg.types.json import Jsonb
from test.test_first_rise_j_wiring_e2e import WiringE2E,at
from src.first_rise_breakout.v2_capacity_repository import CapacityMonitor,capacity_day


class CapacityE2E(WiringE2E):
    def test_additive_capacity_seed_preserves_operator_values(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        self.c.execute('DROP TABLE pg_temp.common_code')
        for name in ('01_common_code_group.sql','02_common_code.sql'):
            sql=(root/'database/ddl'/name).read_text(encoding='utf-8')
            self.c.execute(sql.replace('CREATE TABLE IF NOT EXISTS','CREATE TEMP TABLE'))
        sql=(root/'database/migrations/20261004_first_rise_v2_capacity_config.sql').read_text(encoding='utf-8')
        sql=sql.replace('BEGIN;','').replace('COMMIT;','')
        self.c.execute(sql)
        self.assertEqual(self.c.execute("SELECT attr1,attr2 FROM common_code WHERE group_cd='FIRST_RISE_CAPACITY'").fetchone(),('10','10000000'))
        self.c.execute("UPDATE common_code SET attr1='5' WHERE group_cd='FIRST_RISE_CAPACITY'")
        self.c.execute(sql)
        self.assertEqual(self.c.execute("SELECT attr1 FROM common_code WHERE group_cd='FIRST_RISE_CAPACITY'").fetchone()[0],'5')

    def test_durable_warning_recovery_rebreach_and_no_repeat(self):
        self.cycle();self.fill('1');self.cycle();self.market_stop();self.cycle();self.fill('2');self.cycle()
        monitor=CapacityMonitor(self.pool);monitor.refresh(at=self.now);self.c.commit()
        # Session TEMP synthetic matched samples exercise only the rolling ledger.
        for i in range(1,100):
            from uuid import uuid4
            trade,signal=uuid4(),uuid4()
            for table,changes in (
                ('first_rise_j_capital_binding',{'trade_id':str(trade)}),
                ('first_rise_j_market_signal',{'market_signal_id':str(signal),'stock_code':str(i),'entry_event_key':str(signal)}),
                ('first_rise_j_shadow_trade',{'market_signal_id':str(signal)}),
                ('first_rise_j_capacity_observation',{'trade_id':str(trade),'market_signal_id':str(signal)})):
                self.c.execute('INSERT INTO '+table+' SELECT (jsonb_populate_record(NULL::'+table+',to_jsonb(t)||%s)).* FROM '+table+' t LIMIT 1',(Jsonb(changes),))
        with self.c.transaction(),self.c.cursor() as q:
            config=capacity_day(q,self.now.date())
            q.execute('UPDATE first_rise_j_capacity_observation SET shadow_net_return=.01,live_return_provisional=.008,live_return_final=NULL')
            monitor._warnings(q,config);monitor._warnings(q,config)
            self.assertEqual(q.execute('SELECT count(*) FROM first_rise_capacity_alert').fetchone()[0],2)
            q.execute('UPDATE first_rise_j_capacity_observation SET live_return_provisional=.01')
            monitor._warnings(q,config)
            q.execute('UPDATE first_rise_j_capacity_observation SET live_return_provisional=.0095')
            monitor._warnings(q,config);monitor._warnings(q,config)
            self.assertEqual(q.execute('SELECT count(*) FROM first_rise_capacity_alert').fetchone()[0],4)
        self.c.commit()
        sent=[]
        from types import SimpleNamespace
        monitor.notifier=SimpleNamespace(send=lambda **kw:sent.append(kw))
        monitor.deliver();monitor.deliver();self.assertEqual(len(sent),4)

    def test_shadow_cost_match_and_final_delta_no_double_pnl(self):
        self.cycle();self.fill('1');self.cycle();self.market_stop();self.cycle();self.fill('2',price=1029);self.cycle()
        monitor=CapacityMonitor(self.pool)
        monitor.refresh(at=self.now);self.c.commit()
        row=self.query('SELECT comparison_status,shadow_net_return,live_return_provisional FROM first_rise_j_capacity_observation')[0]
        self.assertEqual(row[0],'PROVISIONAL');self.assertIsNotNone(row[1]);self.assertIsNotNone(row[2])
        trade=self.query('SELECT trade_id FROM first_rise_j_live_cost')[0][0]
        cost_id=self.finalized(trade,actual_cost=31420);self.final(cost_id)
        monitor.refresh(at=self.now);self.c.commit()
        self.assertEqual(self.query('SELECT comparison_status FROM first_rise_j_capacity_observation')[0][0],'FINAL')
        before=self.query('SELECT realized_net_pnl FROM first_rise_j_capital_epoch')
        monitor.refresh(at=self.now);self.c.commit()
        self.assertEqual(self.query('SELECT realized_net_pnl FROM first_rise_j_capital_epoch'),before)
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_capacity_observation')[0][0],1)
        from pathlib import Path
        sql=(Path(__file__).resolve().parents[1]/'database/verification/first_rise_v2_capacity_readonly.sql').read_text(encoding='utf-8')
        result=self.c.execute(sql)
        while True:
            if result.description:result.fetchall()
            if not result.nextset():break

    def test_v2_slot_over_100m_and_80m_liquidity(self):
        self.load()
        self.c.execute('UPDATE first_rise_j_capital_epoch SET realized_net_pnl=140000000')
        self.c.execute("UPDATE first_rise_j_market_signal SET entry_evidence=jsonb_set(entry_evidence,'{entry_liquidity,recent_5m_traded_amount}','\"800000000\"')")
        self.c.commit();self.cash=D(500000000);self.cycle()
        evidence=self.query('SELECT sizing_evidence FROM first_rise_j_live_intent')[0][0]
        self.assertEqual(D(evidence['common_slot_amount']),150000000)
        self.assertEqual(D(evidence['target_cash']),80000000)

    def test_actual_request_can_exceed_legacy_100m(self):
        self.load()
        self.c.execute('UPDATE first_rise_j_capital_epoch SET realized_net_pnl=140000000')
        self.c.commit();self.cash=D(500000000);self.cycle()
        capital,detail=self.query("SELECT strategy_capital_before,detail FROM live_order_request WHERE side='BUY'")[0]
        self.assertEqual(capital,150000000)
        self.assertEqual(D(detail['target_cash']),150000000)
        self.assertGreater(D(detail['estimated_cash_used']),100000000)

    def test_missing_liquidity_preserves_market_shadow_and_exit(self):
        self.c.execute("UPDATE first_rise_j_market_signal SET entry_evidence=jsonb_set(entry_evidence,'{entry_liquidity}',%s)",
                       (Jsonb({'reason':'LIQUIDITY_5M_INSUFFICIENT_BARS'}),));self.c.commit()
        self.cycle();self.assertEqual(len(self.posts),0)
        self.market_stop()
        self.assertEqual(self.query('SELECT exit_reason FROM first_rise_j_market_signal')[0][0],'STOP_ENTRY_BREAK')
        self.assertIsNotNone(self.query('SELECT net_return FROM first_rise_j_shadow_trade')[0][0])

    def test_capacity_daily_restart_immutable(self):
        with self.c.transaction(),self.c.cursor() as q:
            first=capacity_day(q,self.now.date())
            q.execute("UPDATE common_code SET attr1='5' WHERE group_cd='FIRST_RISE_CAPACITY'")
        self.restart()
        with self.c.transaction(),self.c.cursor() as q:
            self.assertEqual(capacity_day(q,self.now.date()),first)

    def test_shadow_without_actual_trade(self):
        self.cash=D(0);self.cycle();self.market_stop()
        self.assertEqual(len(self.posts),0)
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_shadow_trade WHERE net_return IS NOT NULL')[0][0],1)
        CapacityMonitor(self.pool).refresh(at=self.now);self.c.commit()
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_capacity_observation')[0][0],0)
