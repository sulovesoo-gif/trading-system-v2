"""Durable J epoch boundaries; never reads or writes another strategy's capital.

START changes create epochs only when the next daily config becomes effective.
STEP/MAX changes append a daily rule, not an epoch. Pending ENTRY binds its
epoch before submit; actual settlements always follow that immutable binding.
There is deliberately no total capital allocation or broker transport here.
"""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg.types.json import Jsonb

from .config import FirstRiseRuntimeConfig
from .j_capital import finite_decimal
import logging

LOGGER = logging.getLogger(__name__)


def daily_config(pool, *, business_date, loaded_at):
    if loaded_at.date() != business_date:
        raise ValueError('FIRST_RISE_CONFIG_BUSINESS_DATE_MISMATCH')
    # Lock also serializes the first load by signal and execution processes.
    # Errors are persisted too, so a same-day restart cannot apply a mid-day fix.
    with pool.connection() as c, c.transaction(), c.cursor() as q:
        q.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_j_daily_config'))")
        q.execute('SELECT config_row,config_error FROM first_rise_j_runtime_day WHERE business_date=%s',
                  (business_date,))
        saved = q.fetchone()
        if saved is None:
            q.execute("""SELECT use_yn,attr1,attr2,attr3,attr4,attr5,attr6,attr7 FROM common_code
                WHERE group_cd='FIRST_RISE_RUNTIME' AND code='DEFAULT'""")
            rows = q.fetchall()
            row = list(rows[0]) if len(rows) == 1 else []
            error = None
            try:
                if len(rows) != 1:
                    raise ValueError('FIRST_RISE_RUNTIME/DEFAULT must have exactly one row')
                config = FirstRiseRuntimeConfig.from_row(row)
            except ValueError as problem:
                error = str(problem)
            q.execute('''INSERT INTO first_rise_j_runtime_day(business_date,config_row,config_error,loaded_at)
                VALUES(%s,%s,%s,%s)''', (business_date,Jsonb(row),error,loaded_at))
        else:
            row,error = saved
            if error is None:
                config = FirstRiseRuntimeConfig.from_row(row)
    # Raise outside the transaction: retain the day's invalid config snapshot.
    if error is not None:
        raise ValueError(error)
    return config


@dataclass(frozen=True)
class EpochState:
    epoch_id: UUID
    epoch_no: int
    start_slot_amount: Decimal
    slot_step_amount: Decimal
    max_slot_amount: Decimal
    realized_net_pnl: Decimal
    compound_reference: Decimal
    common_slot_amount: Decimal
    revision: int


EPOCH_COLUMNS = '''epoch_id,epoch_no,start_slot_amount,slot_step_amount,max_slot_amount,
    realized_net_pnl,compound_reference,common_slot_amount,revision'''


class JCapitalEpochRepository:
    def __init__(self, pool):
        self.pool = pool

    def begin_day(self, *, business_date: date, loaded_at: datetime):
        config = daily_config(self.pool,business_date=business_date,loaded_at=loaded_at)
        with self.pool.connection() as c, c.transaction(), c.cursor() as q:
            q.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_j_capital_epoch'))")
            q.execute('SELECT epoch_id FROM first_rise_j_epoch_rule_day WHERE business_date=%s',(business_date,))
            known = q.fetchone()
            if known is not None:
                q.execute('SELECT '+EPOCH_COLUMNS+' FROM first_rise_j_capital_epoch WHERE epoch_id=%s',(known[0],))
                return config, EpochState(*q.fetchone())
            q.execute('SELECT max(business_date) FROM first_rise_j_epoch_rule_day')
            latest_day = q.fetchone()[0]
            if latest_day is not None and business_date < latest_day:
                raise ValueError('FIRST_RISE_EPOCH_BACKDATED_CONFIG_FORBIDDEN')
            q.execute('SELECT '+EPOCH_COLUMNS+' FROM first_rise_j_capital_epoch WHERE ended_at IS NULL FOR UPDATE')
            current = q.fetchone()
            current = EpochState(*current) if current else None
            if current is None or current.start_slot_amount != config.start_slot_amount:
                epoch_no = current.epoch_no+1 if current else 1
                epoch_id = uuid5(NAMESPACE_URL,f'FIRST_RISE_J_EPOCH|{business_date.isoformat()}|{epoch_no}')
                if current:
                    q.execute('UPDATE first_rise_j_capital_epoch SET ended_at=%s,revision=revision+1 WHERE epoch_id=%s',
                              (loaded_at,current.epoch_id))
                q.execute('''INSERT INTO first_rise_j_capital_epoch
                    (epoch_id,epoch_no,effective_business_date,effective_at,start_slot_amount,
                     initial_step_amount,initial_max_amount,slot_step_amount,max_slot_amount)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (epoch_id,epoch_no,business_date,loaded_at,config.start_slot_amount,
                     config.slot_step_amount,config.max_slot_amount,config.slot_step_amount,config.max_slot_amount))
            else:
                epoch_id = current.epoch_id
                if (current.slot_step_amount,current.max_slot_amount) != (config.slot_step_amount,config.max_slot_amount):
                    q.execute('''UPDATE first_rise_j_capital_epoch SET slot_step_amount=%s,max_slot_amount=%s,
                        revision=revision+1 WHERE epoch_id=%s''',
                        (config.slot_step_amount,config.max_slot_amount,epoch_id))
            q.execute('''INSERT INTO first_rise_j_epoch_rule_day
                (business_date,epoch_id,slot_step_amount,max_slot_amount) VALUES(%s,%s,%s,%s)''',
                (business_date,epoch_id,config.slot_step_amount,config.max_slot_amount))
            q.execute('SELECT '+EPOCH_COLUMNS+' FROM first_rise_j_capital_epoch WHERE epoch_id=%s',(epoch_id,))
            return config, EpochState(*q.fetchone())

    @staticmethod
    def bind_entry(q, *, trade_id, expected_epoch_id, business_date, stock_code, sizing_evidence, at):
        """Use caller's ENTRY transaction, before request/claim, never after fill.

        Binding cannot be reassigned on restart. Caller must have refreshed the
        day's context and must roll back request creation if this check fails.
        """
        if at.date() != business_date:
            raise ValueError('FIRST_RISE_ENTRY_DATE_MISMATCH')
        q.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_j_capital_epoch'))")
        q.execute('''SELECT epoch_id FROM first_rise_j_epoch_rule_day WHERE business_date=%s''',(business_date,))
        rule = q.fetchone()
        if rule is None or rule[0] != expected_epoch_id:
            raise ValueError('FIRST_RISE_ENTRY_EPOCH_MISMATCH')
        q.execute('SELECT epoch_id,stock_code,entry_business_date FROM first_rise_j_capital_binding WHERE trade_id=%s',(trade_id,))
        prior = q.fetchone()
        if prior:
            if prior != (expected_epoch_id,stock_code,business_date):
                raise ValueError('FIRST_RISE_ENTRY_OWNERSHIP_IMMUTABLE')
            return
        q.execute('SELECT ended_at FROM first_rise_j_capital_epoch WHERE epoch_id=%s FOR UPDATE',(expected_epoch_id,))
        epoch = q.fetchone()
        if epoch is None or epoch[0] is not None:
            raise ValueError('FIRST_RISE_NEW_ENTRY_RETIRED_EPOCH')
        q.execute('''INSERT INTO first_rise_j_capital_binding
            (trade_id,epoch_id,entry_business_date,stock_code,entry_sizing_evidence,created_at)
            VALUES(%s,%s,%s,%s,%s,%s)''',
            (trade_id,expected_epoch_id,business_date,stock_code,Jsonb(sizing_evidence),at))

    @staticmethod
    def entry_state(q, *, epoch_id, business_date):
        """Read latest actual realized PnL under ENTRY transaction lock.

        Do not cache realized PnL for a whole day: automatic compounding applies
        to the next entry immediately; only common_code config is daily-frozen.
        """
        q.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_j_capital_epoch'))")
        q.execute('SELECT epoch_id FROM first_rise_j_epoch_rule_day WHERE business_date=%s',(business_date,))
        rule=q.fetchone()
        if rule is None or rule[0] != epoch_id:
            raise ValueError('FIRST_RISE_ENTRY_EPOCH_MISMATCH')
        q.execute('SELECT '+EPOCH_COLUMNS+' FROM first_rise_j_capital_epoch WHERE epoch_id=%s AND ended_at IS NULL FOR UPDATE',(epoch_id,))
        row=q.fetchone()
        if row is None:
            raise ValueError('FIRST_RISE_NEW_ENTRY_RETIRED_EPOCH')
        return EpochState(*row)

    def settle_actual(self, *, event_key, trade_id, net_pnl_delta, settled_at, evidence):
        """Idempotent actual net settlement/cost adjustment; never simulated PnL.

        event_key is supplied by canonical fill/cost settlement; a duplicate
        with differing amount/ownership is an error, not silently ignored.
        OPEN/PENDING bindings remain immutable even after their epoch retires.
        """
        delta = finite_decimal(net_pnl_delta,'actual_net_pnl_delta')
        if not event_key or not evidence:
            raise ValueError('FIRST_RISE_ACTUAL_SETTLEMENT_EVIDENCE_REQUIRED')
        with self.pool.connection() as c, c.transaction(), c.cursor() as q:
            q.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_j_capital_epoch'))")
            q.execute('SELECT epoch_id FROM first_rise_j_capital_binding WHERE trade_id=%s',(trade_id,))
            binding = q.fetchone()
            if binding is None:
                raise ValueError('FIRST_RISE_TRADE_EPOCH_BINDING_REQUIRED')
            epoch_id = binding[0]
            q.execute('SELECT trade_id,epoch_id,actual_net_pnl_delta FROM first_rise_j_realized_event WHERE event_key=%s',(event_key,))
            prior = q.fetchone()
            if prior:
                if prior != (trade_id,epoch_id,delta):
                    raise ValueError('FIRST_RISE_SETTLEMENT_IDEMPOTENCY_CONFLICT')
                return False
            q.execute('''INSERT INTO first_rise_j_realized_event
                (event_key,trade_id,epoch_id,actual_net_pnl_delta,settled_at,evidence)
                VALUES(%s,%s,%s,%s,%s,%s)''',(event_key,trade_id,epoch_id,delta,settled_at,Jsonb(evidence)))
            # Deliberately no ended_at IS NULL: old OPEN settles in old epoch.
            q.execute('''UPDATE first_rise_j_capital_epoch SET realized_net_pnl=realized_net_pnl+%s,
                revision=revision+1 WHERE epoch_id=%s''',(delta,epoch_id))
            return True


class DailyCapitalContext:
    """Execution process daily config/epoch identity cache, not a SEND gate.

    No retry of invalid config mid-day. EXIT settlement uses the repository's
    immutable binding and is independent of this new-entry context.
    """
    def __init__(self, repository):
        self.repository = repository
        self.business_date = None
        self.config = None
        self.epoch_id = None

    def load(self, *, at):
        if self.business_date == at.date():
            return
        self.business_date = at.date()
        self.config = self.epoch_id = None
        try:
            config,epoch = self.repository.begin_day(business_date=at.date(),loaded_at=at)
        except Exception:
            LOGGER.exception('FIRST_RISE_J_CAPITAL_CONFIG_ERROR new ENTRY blocked; old epoch EXIT remains active')
            return
        self.config, self.epoch_id = config, epoch.epoch_id
