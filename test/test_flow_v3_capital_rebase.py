"""Local PostgreSQL only. No .env, broker client, HTTP, production settings or services."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal as D
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from src.flow_v3.live_operations import LiveOperations
from src.flow_v3.live_repository import LiveRepository
from test import test_flow_v3_send_authorization as send_tests

ROOT = Path(__file__).resolve().parents[1]


class CapitalPostGuardTests(unittest.TestCase):
    def test_changed_capital_after_claim_no_http(self):
        class Changed(send_tests.Connection):
            def execute(self, query, args=None):
                result = super().execute(query, args)
                if 'SELECT live_approved' in query:
                    self.row = None  # Final capital predicate no longer matches.
                return result
        conn = Changed()
        sent, calls, _ = send_tests.SendTests().run_fake(conn)
        self.assertEqual((sent, calls), (0, []))
        guard = next(s for s, _ in conn.statements if 'SELECT live_approved' in s)
        self.assertIn('allocated_amount=capital.initial_capital', guard)
        self.assertIn('i.capital_at_entry=capital.current_capital', guard)
        self.assertIn('FOR SHARE OF op,capital', guard)


@unittest.skipUnless(os.getenv('FLOW_CAPITAL_TEST_DSN'), 'isolated localhost PostgreSQL opt-in')
class CapitalDatabaseTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        dsn = os.environ['FLOW_CAPITAL_TEST_DSN']
        cfg = conninfo_to_dict(dsn)
        self.assertEqual((cfg.get('host'), cfg.get('port'), cfg.get('dbname')),
                         ('127.0.0.1', '55491', 'flow_route_test'))
        self.c = c = psycopg.connect(dsn, autocommit=True)
        self.addCleanup(c.close)
        self.env = patch.dict(os.environ, {'FLOW_V3_ACTUAL_SEND': 'N'})
        self.env.start(); self.addCleanup(self.env.stop)
        # Unique schema, never clean/delete any previous local or production tables.
        self.schema = 'capital_contract_' + uuid4().hex
        c.execute('CREATE SCHEMA ' + self.schema)
        c.execute('SET search_path TO ' + self.schema)
        c.execute((ROOT/'test/fixtures/flow_v3_routes_legacy.sql').read_text())
        for name in ('20260909_flow_v3_live_preparation.sql', '20260909_flow_v3_live_pipeline.sql',
                     '20260910_flow_v3_send_authorization.sql', '20260913_flow_v3_live_routes.sql',
                     '20260914_flow_v3_capital_change.sql', '20260915_flow_v3_checkpoint_execution_code_check.sql'):
            c.execute((ROOT/'database/migrations'/name).read_text())
        c.execute("INSERT INTO flow_v3_strategy_master VALUES('CAPITAL_FIXTURE','000660','LONG','0193T0','Y')")
        c.autocommit = False
        class Pool:
            @contextmanager
            def connection(self):
                try:
                    yield c
                    c.commit()
                except Exception:
                    c.rollback()
                    raise
        self.pool = Pool()
        self.admin = LiveOperations(self.pool)
        self.repo = LiveRepository(self.pool, lambda *a: None)
        self.now = datetime(2026, 9, 16, 10)
        self.event_id = 0
        self.price = D(250000)
        self.op = self.admin.set_capital('CAPITAL_FIXTURE', 'UNDERLYING', '6000000', 'INITIAL',
                                         self.now-timedelta(minutes=1))['operation_id']

    def row(self, query, args=()):
        row = self.c.execute(query, args).fetchone()
        self.c.commit()
        return row

    def cycle(self, at=None):
        at = at or self.now
        return self.repo.cycle(at, {'000660': (self.price, at)})

    def entry(self, op=None, *, policy='SIGNAL_HOLD'):
        op = op or self.op
        self.event_id += 1
        event = self.event_id
        t = self.now
        self.c.execute("INSERT INTO flow_v3_paper_trade VALUES(%s,'UNCHANGED')", (event,))
        self.c.execute("""INSERT INTO flow_v3_runtime_entry_event VALUES
            (%s,'CAPITAL_FIXTURE',%s,%s,%s,%s,'000660','LONG','0193T0',%s,'F1',1,3)""",
            (event, 'EVENT'+str(event), event, t, t, policy))
        self.c.commit()
        self.now += timedelta(minutes=1)
        self.cycle()
        return event, self.order(op, event, 'BUY')

    def order(self, op, event, side):
        return self.row("""SELECT o.broker_order_id,i.quantity FROM flow_v3_live_order o
            JOIN flow_v3_live_intent i USING(intent_id)
            WHERE i.operation_id=%s AND i.event_id=%s AND i.side=%s""", (op, event, side))

    def observe(self, order, side, amount):
        oid, qty = order
        number = str(oid)
        self.repo.record_response(oid, {'rt_cd': '0', 'output': {'ODNO': number}}, self.now)
        return self.repo.observe(oid, order_number=number, order_date=self.now.date(), stock_code='000660',
            side=side, requested_quantity=qty, filled_quantity=qty, filled_amount=D(amount),
            status='FILLED', observed_at=self.now, remaining_quantity=0)['live_trade_id']

    def close(self, op, event, buy, buy_amount, net, *, eod=False):
        if eod:
            self.now = self.now.replace(hour=15, minute=19, second=1)
            self.c.execute("INSERT INTO flow_v3_runtime_cursor VALUES('000660',%s)",
                           (self.now.replace(minute=18, second=0),))
        else:
            self.now += timedelta(minutes=1)
            self.c.execute("INSERT INTO flow_v3_minute_state VALUES('000660',%s,true,'{\"01\":-1}','{}')",
                           (self.now,))
            self.now += timedelta(minutes=1)
        self.c.commit()
        self.cycle()  # Confirm terminal ENTRY history after EXIT request.
        self.observe(buy, 'BUY', buy_amount)
        self.cycle()
        sell = self.order(op, event, 'SELL')
        self.assertEqual(sell[1], buy[1])
        tid = self.observe(sell, 'SELL', D(buy_amount)+D(net))
        self.c.execute("INSERT INTO broker_shared_cost_snapshot VALUES(%s,'000660','FINALIZED_BY_STABLE_RECHECK')",
                       (self.now.date(),))
        for side, amount in (('BUY', D(buy_amount)), ('SELL', D(buy_amount)+D(net))):
            self.c.execute("INSERT INTO broker_shared_cost_allocation VALUES(%s,'000660','FLOW',%s,%s,%s,0,0,0,0)",
                           (self.now.date(), tid, side, amount))
        self.c.commit()
        self.cycle()
        return tid

    def capital(self, op):
        return self.row('SELECT initial_capital,current_capital,realized_net FROM flow_v3_live_capital WHERE operation_id=%s', (op,))

    def rebase(self, amount, ref='REBASE'):
        self.now += timedelta(seconds=1)
        return self.admin.set_capital('CAPITAL_FIXTURE', 'UNDERLYING', str(amount), ref, self.now)['operation_id']

    def test_six_million_and_realized_profit_quantity(self):
        event, buy = self.entry()
        self.assertEqual(buy[1], 24)
        self.observe(buy, 'BUY', 6000000)
        self.close(self.op, event, buy, 6000000, 100000)
        self.assertEqual(self.capital(self.op), (6000000, 6100000, 100000))
        self.price = D(100000)
        _, next_buy = self.entry()
        self.assertEqual(next_buy[1], 61)  # Not capped at the approved base's 60 shares.
        self.assertEqual(self.row("SELECT capital_at_entry FROM flow_v3_live_intent WHERE operation_id=%s ORDER BY signal_time DESC LIMIT 1", (self.op,))[0], 6100000)

    def test_increase_rebase_then_profit_and_no_old_cap(self):
        # Existing realized history fixture; preserve capital invariant.
        self.c.execute('UPDATE flow_v3_live_capital SET current_capital=6350000,realized_net=350000 WHERE operation_id=%s', (self.op,))
        self.c.commit()
        new = self.rebase(8000000)
        self.assertNotEqual(new, self.op)
        self.assertEqual(self.capital(self.op), (6000000, 6350000, 350000))
        self.assertEqual(self.capital(new), (8000000, 8000000, 0))
        event, buy = self.entry(new)
        self.assertEqual(buy[1], 32)
        self.observe(buy, 'BUY', 8000000)
        self.close(new, event, buy, 8000000, 200000)
        self.assertEqual(self.capital(new), (8000000, 8200000, 200000))
        self.price = D(100000)
        _, later = self.entry(new)
        self.assertEqual(later[1], 82)  # Not capped at 80 shares.
        self.assertEqual(self.row("SELECT capital_at_entry FROM flow_v3_live_intent WHERE operation_id=%s AND side='BUY' ORDER BY signal_time DESC LIMIT 1", (new,))[0], 8200000)
        self.assertEqual(self.rebase(8000000, 'SAME_AMOUNT'), new)
        self.assertEqual(self.capital(new)[1], 8200000)

    def test_decrease_rebase(self):
        self.c.execute('UPDATE flow_v3_live_capital SET current_capital=6350000,realized_net=350000 WHERE operation_id=%s', (self.op,))
        self.c.commit()
        new = self.rebase(4000000)
        self.assertEqual(self.entry(new)[1][1], 16)
        self.assertEqual(self.capital(self.op)[1], 6350000)

    def test_direct_sql_stale_capital_blocks_then_explicit_rebase_quantity_zero(self):
        self.c.execute('UPDATE flow_v3_strategy_operation SET allocated_amount=1000 WHERE operation_id=%s', (self.op,))
        self.c.commit()
        event, buy = self.entry()
        self.assertIsNone(buy)
        self.assertEqual(self.row('SELECT status,reason FROM flow_v3_live_intent WHERE operation_id=%s AND event_id=%s',
                                 (self.op, event)), ('BLOCKED', 'CAPITAL_REBASE_REQUIRED'))
        new = self.rebase(1000)  # Same SQL amount is NOT a no-op when epoch basis differs.
        self.assertNotEqual(new, self.op)
        self.assertEqual(self.capital(new), (1000, 1000, 0))
        event, buy = self.entry(new)
        self.assertIsNone(buy)
        self.assertEqual(self.row('SELECT reason FROM flow_v3_live_intent WHERE operation_id=%s AND event_id=%s', (new, event))[0], 'QUANTITY_ZERO')

    def test_kill_switch_stays_false_zero_same_and_changed_amount(self):
        self.admin.set_entry(self.op, False, self.now)
        self.assertEqual(self.rebase(6000000, 'SAME'), self.op)
        self.assertIsNone(self.entry()[1])
        self.assertEqual(self.rebase(0, 'ZERO'), self.op)
        new = self.rebase(8000000, 'POSITIVE')
        self.assertFalse(self.row('SELECT entry_enabled FROM flow_v3_strategy_operation WHERE operation_id=%s', (new,))[0])
        self.assertIsNone(self.entry(new)[1])

    def test_waiting_reference_capital_changed_before_order_preparation(self):
        # No usable quote yet: ENTRY exists, but no order has been prepared.
        self.price = D(0)
        event, buy = self.entry()
        self.assertIsNone(buy)
        self.assertEqual(self.row('SELECT status FROM flow_v3_live_intent WHERE event_id=%s', (event,))[0], 'WAITING_REFERENCE')
        self.c.execute('UPDATE flow_v3_strategy_operation SET allocated_amount=1000 WHERE operation_id=%s', (self.op,))
        self.c.commit()
        self.price = D(250000)
        self.cycle()
        self.assertIsNone(self.order(self.op, event, 'BUY'))
        self.assertEqual(self.row('SELECT status,reason FROM flow_v3_live_intent WHERE event_id=%s', (event,)),
                         ('BLOCKED', 'CAPITAL_REBASE_REQUIRED'))
        self.assertEqual(self.row('SELECT next_quantity,last_error FROM flow_v3_live_capital WHERE operation_id=%s',
                                 (self.op,)), (0, 'CAPITAL_REBASE_REQUIRED'))
        new = self.rebase(1000)
        self.cycle()
        self.assertEqual(self.row('SELECT count(*) FROM flow_v3_live_intent WHERE operation_id=%s AND event_id=%s',
                                 (new, event))[0], 0)  # Never replay an old blocked ENTRY into the new epoch.

    def test_zero_keeps_enabled_but_no_buy_and_normal_exit_settles(self):
        event, buy = self.entry()
        self.observe(buy, 'BUY', 6000000)
        self.rebase(0)
        self.assertTrue(self.row('SELECT entry_enabled FROM flow_v3_strategy_operation WHERE operation_id=%s', (self.op,))[0])
        self.assertIsNone(self.entry()[1])
        self.close(self.op, event, buy, 6000000, 100000)
        self.assertEqual(self.capital(self.op)[1], 6100000)

    def test_zero_preserves_eod_exit_and_settlement(self):
        event, buy = self.entry(policy='SIGNAL_EOD')
        self.observe(buy, 'BUY', 6000000)
        self.rebase(0)
        self.close(self.op, event, buy, 6000000, 100000, eod=True)
        self.assertEqual(self.capital(self.op)[1], 6100000)
        self.assertEqual(self.row("SELECT exit_reason FROM flow_v3_live_intent WHERE operation_id=%s AND side='SELL'", (self.op,))[0], 'SIGNAL_EOD')

    def test_open_lot_exit_settlement_stays_on_old_epoch(self):
        event, buy = self.entry()
        tid = self.observe(buy, 'BUY', 6000000)
        new = self.rebase(8000000)
        self.assertEqual(self.row('SELECT operation_id FROM flow_v3_live_lot WHERE live_trade_id=%s', (tid,))[0], self.op)
        self.close(self.op, event, buy, 6000000, 100000)
        self.assertEqual(self.capital(self.op)[1], 6100000)
        self.assertEqual(self.capital(new), (8000000, 8000000, 0))
        self.assertEqual(self.row('SELECT operation_id FROM flow_v3_live_settlement WHERE live_trade_id=%s', (tid,))[0], self.op)
        self.assertEqual(self.entry(new)[1][1], 32)

    def test_kis_insufficient_cash_not_replayed(self):
        self.repo.cash_check = lambda *a: 'KIS_ORDERABLE_CASH_INSUFFICIENT'
        event, buy = self.entry()
        self.assertIsNone(buy)
        self.repo.cash_check = lambda *a: None
        self.cycle()
        self.assertIsNone(self.order(self.op, event, 'BUY'))
        self.assertEqual(self.row('SELECT reason FROM flow_v3_live_intent WHERE event_id=%s', (event,))[0], 'KIS_ORDERABLE_CASH_INSUFFICIENT')

    def test_same_amount_no_churn_reference_idempotent(self):
        self.assertEqual(self.rebase(6000000, 'UNCHANGED'), self.op)
        self.assertEqual(self.rebase(6000000, 'UNCHANGED'), self.op)
        self.assertEqual(self.row('SELECT count(*) FROM flow_v3_strategy_operation')[0], 1)


if __name__ == '__main__': unittest.main()
