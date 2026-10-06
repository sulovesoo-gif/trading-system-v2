"""Transactional J market state and independent research fills.

Legacy PAPER rows and their candidate UNIQUE constraints remain untouched.
This repository never creates LIVE requests or broker orders.
"""
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from psycopg.types.json import Jsonb

from .j_signal import JState
from .models import CandidateState, ResearchState
from .repository import stable_id
from .strategy import FirstRiseBreakoutStrategy


def _json_value(value):
    if isinstance(value, (datetime, date, UUID, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return {k:_json_value(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    return value


def encode_state(state):
    return _json_value(asdict(state))


def _candidate(data):
    if data is None:
        return None
    data = dict(data)
    data['candidate_event_id'] = UUID(data['candidate_event_id'])
    data['business_date'] = date.fromisoformat(data['business_date'])
    data['state'] = ResearchState(data['state'])
    for key in ('peak_time','last_observed_at','entry_signal_time'):
        if data.get(key) is not None:
            data[key] = datetime.fromisoformat(data[key])
    for key in ('peak_price','pullback_low_price','pullback_pct','last_observed_price',
                'raw_entry_price','entry_execution_price'):
        if data.get(key) is not None:
            data[key] = Decimal(data[key])
    return CandidateState(**data)


def decode_state(data):
    return JState(tracking=_candidate(data['tracking']), sequence=data['sequence'],
        open_signal=_candidate(data['open_signal']), prior_signal_key=data['prior_signal_key'],
        prior_exit_reason=data['prior_exit_reason'],
        prior_exit_time=datetime.fromisoformat(data['prior_exit_time']) if data['prior_exit_time'] else None)


class JMarketRepository:
    def __init__(self, pool):
        self.pool = pool

    def roster(self, *, business_date):
        with self.pool.connection() as c, c.cursor() as q:
            q.execute('''SELECT j.state_snapshot,j.revision,e.discovered_at
                FROM first_rise_j_candidate j
                JOIN first_rise_breakout_candidate_event e ON e.candidate_event_id=j.candidate_event_id
                WHERE j.business_date=%s ORDER BY e.discovered_at,j.stock_code''', (business_date,))
            return [(decode_state(s),int(v),at) for s,v,at in q.fetchall()]

    def load_or_create(self, candidate):
        initial = JState(candidate)
        with self.pool.connection() as c, c.transaction(), c.cursor() as q:
            q.execute('''INSERT INTO first_rise_j_candidate
                (candidate_event_id,business_date,stock_code,state_snapshot)
                VALUES(%s,%s,%s,%s) ON CONFLICT(candidate_event_id) DO NOTHING''',
                (candidate.candidate_event_id,candidate.business_date,candidate.stock_code,Jsonb(encode_state(initial))))
            q.execute('SELECT state_snapshot,revision FROM first_rise_j_candidate WHERE candidate_event_id=%s',
                      (candidate.candidate_event_id,))
            snapshot, revision = q.fetchone()
            return decode_state(snapshot), int(revision)

    def save(self, step, *, expected_revision):
        """CAS prevents duplicate market/PAPER events across refresh/restart."""
        state = step.state
        cid = state.tracking.candidate_event_id
        with self.pool.connection() as c, c.transaction(), c.cursor() as q:
            q.execute('SELECT revision FROM first_rise_j_candidate WHERE candidate_event_id=%s FOR UPDATE',(cid,))
            row = q.fetchone()
            if row is None or row[0] != expected_revision:
                raise RuntimeError('FIRST_RISE_J_STATE_VERSION_CONFLICT')
            if step.market_entry is not None:
                decision = step.market_entry
                signal_id = stable_id('FIRST_RISE_J|'+decision.after.entry_event_key)
                evidence = decision.evidence
                prior_key = evidence.get('prior_market_signal_key')
                prior_id = stable_id('FIRST_RISE_J|'+prior_key) if prior_key else None
                if state.sequence == 2:
                    q.execute('''SELECT exit_reason,exit_execution_time FROM first_rise_j_market_signal
                        WHERE market_signal_id=%s AND candidate_event_id=%s AND signal_sequence=1''', (prior_id,cid))
                    prior = q.fetchone()
                    if prior is None or prior[0] != 'STOP_ENTRY_BREAK' or prior[1] >= decision.signal_time:
                        raise RuntimeError('FIRST_RISE_J_SECOND_PARENT_INVALID')
                q.execute('''INSERT INTO first_rise_j_market_signal
                    (market_signal_id,candidate_event_id,business_date,stock_code,signal_sequence,
                     prior_market_signal_id,entry_event_key,entry_signal_time,raw_entry_price,entry_evidence)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (signal_id,cid,state.tracking.business_date,state.tracking.stock_code,state.sequence,
                     prior_id,decision.after.entry_event_key,decision.signal_time,decision.raw_execution_price,Jsonb(evidence)))
                if not evidence.get('sequence_replay_only'):
                    self._paper_entry(q, signal_id, decision)
                    from .v2_capacity_repository import shadow_entry
                    shadow_entry(q,signal_id,decision)
            if step.market_exit is not None:
                decision = step.market_exit
                signal_id = stable_id('FIRST_RISE_J|'+decision.before.entry_event_key)
                q.execute('''UPDATE first_rise_j_market_signal SET exit_signal_time=%s,
                    exit_execution_time=%s,raw_exit_price=%s,exit_reason=%s,exit_evidence=%s
                    WHERE market_signal_id=%s AND candidate_event_id=%s AND exit_reason IS NULL''',
                    (decision.signal_time,decision.after.last_observed_at,decision.raw_execution_price,
                     decision.reason,Jsonb(decision.evidence or {}),signal_id,cid))
                if q.rowcount != 1:
                    raise RuntimeError('FIRST_RISE_J_OPEN_SIGNAL_MISSING')
                self._paper_exit(q,signal_id,decision)
                from .v2_capacity_repository import shadow_exit
                # Legacy signals have no V2 Shadow row; their exit remains unchanged.
                q.execute('SELECT entry_evidence FROM first_rise_j_market_signal WHERE market_signal_id=%s',(signal_id,))
                from .v2_capacity import FORMULA_VERSION
                if q.fetchone()[0].get('formula_version')==FORMULA_VERSION:
                    shadow_exit(q,signal_id,decision)
            q.execute('''UPDATE first_rise_j_candidate SET state_snapshot=%s,
                revision=revision+1,updated_at=CURRENT_TIMESTAMP WHERE candidate_event_id=%s''',
                (Jsonb(encode_state(state)),cid))
        return expected_revision+1

    @staticmethod
    def _paper_entry(q, signal_id, decision):
        # Research-only baseline. This lock/slot never controls market or LIVE.
        q.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_j_paper_k1'))")
        status = 'OPEN'
        if not decision.evidence['paper_entry_eligible']:
            status = 'OUTSIDE_PAPER_WINDOW'
        q.execute("SELECT 1 FROM first_rise_j_paper_trade WHERE status='OPEN' LIMIT 1")
        if q.fetchone() and status == 'OPEN':
            status = 'OVERLAP_SKIP'
        q.execute("SELECT 10000000+COALESCE(sum(realized_pnl),0) FROM first_rise_j_paper_trade WHERE status='CLOSED'")
        capital = Decimal(q.fetchone()[0])
        plan = FirstRiseBreakoutStrategy.entry_plan(capital=max(Decimal(0),capital),
                                                   raw_entry_price=decision.raw_execution_price)
        if status == 'OPEN' and plan['quantity'] < 1:
            status = 'NO_CAPITAL'
        qty = plan['quantity'] if status == 'OPEN' else 0
        q.execute('''INSERT INTO first_rise_j_paper_trade
            (market_signal_id,status,quantity,capital_before,cash_remaining,entry_execution_price)
            VALUES(%s,%s,%s,%s,%s,%s)''',
            (signal_id,status,qty,capital,plan['cash_remaining'] if qty else capital,
             FirstRiseBreakoutStrategy.buy_execution_price(decision.raw_execution_price) if qty else None))

    @staticmethod
    def _paper_exit(q, signal_id, decision):
        q.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_j_paper_k1'))")
        q.execute("SELECT quantity,capital_before,cash_remaining FROM first_rise_j_paper_trade WHERE market_signal_id=%s AND status='OPEN' FOR UPDATE",(signal_id,))
        row = q.fetchone()
        if row is None:
            return  # A skipped PAPER fill never prevents independent market EXIT.
        plan = FirstRiseBreakoutStrategy.exit_plan(quantity=int(row[0]),capital_before=row[1],
            cash_remaining=row[2],raw_exit_price=decision.raw_execution_price)
        q.execute('''UPDATE first_rise_j_paper_trade SET status='CLOSED',exit_execution_price=%s,
            realized_pnl=%s,capital_after=%s,updated_at=CURRENT_TIMESTAMP WHERE market_signal_id=%s''',
            (FirstRiseBreakoutStrategy.sell_execution_price(decision.raw_execution_price),
             plan['realized_pnl'],plan['capital_after'],signal_id))
