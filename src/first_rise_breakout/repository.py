"""Additive persistence for first-rise breakout research only."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg.types.json import Jsonb

from .models import CandidateState, Decision, Observation, ResearchState


def stable_id(value: str) -> UUID:
    return uuid5(NAMESPACE_URL, value)


class FirstRiseBreakoutRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    @staticmethod
    def _state(row) -> CandidateState:
        return CandidateState(
            candidate_event_id=row[0], business_date=row[1], stock_code=row[2],
            state=ResearchState(row[3]), peak_price=row[4], peak_time=row[5],
            pullback_low_price=row[6], pullback_pct=row[7], last_observed_at=row[8],
            last_observed_price=row[9], entry_event_key=row[10], version=row[11],
        )

    def record_condition_hits(
        self, *, poll_time: datetime, business_date: date, condition_name: str,
        condition_seq: str, candidates,
    ) -> int:
        inserted = 0
        with self.pool.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            for candidate in candidates:
                hit_key = (
                    f"FRB_CONDITION_HIT|{poll_time.isoformat()}|{condition_name}|"
                    f"{condition_seq}|{candidate.stock_code}"
                )
                cursor.execute(
                    """INSERT INTO first_rise_breakout_condition_hit
                       (condition_hit_id,poll_time,business_date,condition_name,condition_seq,
                        stock_code,stock_name,result_rank,raw_payload)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(poll_time,condition_name,condition_seq,stock_code) DO NOTHING""",
                    (stable_id(hit_key), poll_time, business_date, condition_name, condition_seq,
                     candidate.stock_code, candidate.stock_name, candidate.result_rank,
                     Jsonb(candidate.raw_payload)),
                )
                inserted += cursor.rowcount
        return inserted

    def record_candidate(
        self, *, business_date: date, condition_name: str, condition_seq: str,
        stock_code: str, stock_name: str | None, discovered_at: datetime, raw_payload: dict,
    ) -> tuple[CandidateState, bool]:
        event_key = f"FRB_DISCOVERY|{business_date.isoformat()}|{condition_name}|{stock_code}"
        candidate_id = stable_id(event_key)
        created = False
        with self.pool.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO first_rise_breakout_candidate_event
                   (candidate_event_id,event_key,business_date,condition_name,condition_seq,
                    stock_code,stock_name,discovered_at,raw_payload)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(event_key) DO NOTHING RETURNING candidate_event_id""",
                (candidate_id, event_key, business_date, condition_name, condition_seq,
                 stock_code, stock_name, discovered_at, Jsonb(raw_payload)),
            )
            created = cursor.fetchone() is not None
            if created:
                cursor.execute(
                    """INSERT INTO first_rise_breakout_candidate_state(candidate_event_id,state_code)
                       VALUES(%s,'DISCOVERED')""", (candidate_id,),
                )
                transition_key = f"{event_key}|DISCOVERED"
                cursor.execute(
                    """INSERT INTO first_rise_breakout_state_transition
                       (transition_id,transition_key,candidate_event_id,from_state,to_state,
                        observed_at,reason_code,evidence)
                       VALUES(%s,%s,%s,NULL,'DISCOVERED',%s,'CONDITION_DISCOVERY',%s)""",
                    (stable_id(transition_key), transition_key, candidate_id, discovered_at,
                     Jsonb({"condition_name": condition_name, "condition_seq": condition_seq})),
                )
            cursor.execute(
                """SELECT e.candidate_event_id,e.business_date,e.stock_code,s.state_code,
                          s.peak_price,s.peak_time,s.pullback_low_price,s.pullback_pct,
                          s.last_observed_at,s.last_observed_price,s.entry_event_key,s.state_version
                   FROM first_rise_breakout_candidate_event e
                   JOIN first_rise_breakout_candidate_state s USING(candidate_event_id)
                   WHERE e.event_key=%s""", (event_key,),
            )
            return self._state(cursor.fetchone()), created

    def active_states(self, *, business_date: date) -> list[CandidateState]:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT e.candidate_event_id,e.business_date,e.stock_code,s.state_code,
                          s.peak_price,s.peak_time,s.pullback_low_price,s.pullback_pct,
                          s.last_observed_at,s.last_observed_price,s.entry_event_key,s.state_version
                   FROM first_rise_breakout_candidate_event e
                   JOIN first_rise_breakout_candidate_state s USING(candidate_event_id)
                   WHERE e.business_date=%s AND s.state_code NOT IN ('PAPER_EXITED','REJECTED','EXPIRED')""",
                (business_date,),
            )
            return [self._state(row) for row in cursor.fetchall()]

    def apply(self, decision: Decision, observation: Observation, *, evidence: dict | None = None) -> CandidateState:
        if not decision.changed:
            return decision.before
        before, after = decision.before, decision.after
        transition_key = (
            f"FRB_TRANSITION|{before.candidate_event_id}|{before.version}|"
            f"{after.state.value}|{observation.observed_at.isoformat()}|{decision.reason}"
        )
        with self.pool.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """SELECT state_code,state_version FROM first_rise_breakout_candidate_state
                   WHERE candidate_event_id=%s FOR UPDATE""", (before.candidate_event_id,),
            )
            locked = cursor.fetchone()
            if locked is None:
                raise RuntimeError("candidate state disappeared")
            if int(locked[1]) != before.version:
                cursor.execute(
                    """SELECT e.candidate_event_id,e.business_date,e.stock_code,s.state_code,
                              s.peak_price,s.peak_time,s.pullback_low_price,s.pullback_pct,
                              s.last_observed_at,s.last_observed_price,s.entry_event_key,s.state_version
                       FROM first_rise_breakout_candidate_event e
                       JOIN first_rise_breakout_candidate_state s USING(candidate_event_id)
                       WHERE e.candidate_event_id=%s""", (before.candidate_event_id,),
                )
                return self._state(cursor.fetchone())
            cursor.execute(
                """INSERT INTO first_rise_breakout_state_transition
                   (transition_id,transition_key,candidate_event_id,from_state,to_state,observed_at,
                    observed_price,peak_price,peak_time,pullback_pct,reason_code,evidence)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(transition_key) DO NOTHING""",
                (stable_id(transition_key), transition_key, before.candidate_event_id,
                 before.state.value, after.state.value, observation.observed_at, observation.price,
                 after.peak_price, after.peak_time, after.pullback_pct, decision.reason,
                 Jsonb(evidence or {"source": observation.source})),
            )
            cursor.execute(
                """UPDATE first_rise_breakout_candidate_state SET
                     state_code=%s,peak_price=%s,peak_time=%s,pullback_low_price=%s,
                     pullback_pct=%s,last_observed_at=%s,last_observed_price=%s,
                     entry_event_key=%s,state_version=%s,updated_at=CURRENT_TIMESTAMP
                   WHERE candidate_event_id=%s AND state_version=%s""",
                (after.state.value, after.peak_price, after.peak_time, after.pullback_low_price,
                 after.pullback_pct, after.last_observed_at, after.last_observed_price,
                 after.entry_event_key, after.version, before.candidate_event_id, before.version),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("candidate state optimistic update failed")
            if decision.create_entry:
                cursor.execute(
                    """INSERT INTO first_rise_breakout_paper_trade
                       (candidate_event_id,entry_event_key,stock_code,trade_status,
                        entry_signal_time,entry_execution_time,entry_execution_price,entry_evidence)
                       VALUES(%s,%s,%s,'OPEN',%s,%s,%s,%s)
                       ON CONFLICT(entry_event_key) DO NOTHING""",
                    (after.candidate_event_id, after.entry_event_key, after.stock_code,
                     observation.observed_at, observation.observed_at, observation.price,
                     Jsonb(evidence or {"source": observation.source})),
                )
            if decision.create_exit:
                exit_key = f"FRB_EXIT|{after.candidate_event_id}|{observation.observed_at.isoformat()}|{decision.reason}"
                cursor.execute(
                    """UPDATE first_rise_breakout_paper_trade SET trade_status='CLOSED',
                         exit_event_key=%s,exit_signal_time=%s,exit_execution_time=%s,
                         exit_execution_price=%s,exit_reason=%s,exit_evidence=%s,
                         updated_at=CURRENT_TIMESTAMP
                       WHERE candidate_event_id=%s AND trade_status='OPEN'""",
                    (exit_key, observation.observed_at, observation.observed_at, observation.price,
                     decision.reason, Jsonb(evidence or {"source": observation.source}),
                     after.candidate_event_id),
                )
            return after
