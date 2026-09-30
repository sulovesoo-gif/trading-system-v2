"""Additive persistence for first-rise breakout research only."""

from __future__ import annotations

from dataclasses import replace
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

    def runtime_config(self):
        from .config import FirstRiseRuntimeConfig
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT use_yn,attr1,attr2,attr3,attr4 FROM common_code
                              WHERE group_cd='FIRST_RISE_RUNTIME' AND code='DEFAULT'""")
            rows = cursor.fetchall()
            if len(rows) != 1:
                raise ValueError("FIRST_RISE_RUNTIME/DEFAULT must have exactly one row")
            return FirstRiseRuntimeConfig.from_row(rows[0])

    @staticmethod
    def _state(row) -> CandidateState:
        return CandidateState(
            candidate_event_id=row[0], business_date=row[1], stock_code=row[2],
            state=ResearchState(row[3]), peak_price=row[4], peak_time=row[5],
            pullback_low_price=row[6], pullback_pct=row[7], last_observed_at=row[8],
            last_observed_price=row[9], entry_event_key=row[10], version=row[11],
            entry_signal_time=(row[12] if len(row) > 12 else None),
            raw_entry_price=(row[13] if len(row) > 13 else None),
            entry_execution_price=(row[14] if len(row) > 14 else None),
        )

    def previous_regular_close(self, *, stock_code: str, business_date: date) -> Decimal | None:
        """Actual prior KRX trading-day close; never historical previous_close_price."""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT close_price::numeric
                   FROM raw_stock_minute
                   WHERE stock_code=%s AND data_source='KIS' AND trading_venue='KRX'
                     AND collect_cycle='1MIN' AND bar_time::date < %s
                     AND bar_time::time BETWEEN TIME '09:00' AND TIME '15:30'
                   ORDER BY bar_time DESC LIMIT 1""",
                (stock_code, business_date),
            )
            row = cursor.fetchone()
            return row[0] if row else None

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
                          s.last_observed_at,s.last_observed_price,s.entry_event_key,s.state_version,
                          p.entry_signal_time,
                          COALESCE((p.entry_evidence->>'raw_entry_price')::numeric,
                                   p.entry_execution_price / (1 + 2.0/10000.0)),
                          p.entry_execution_price
                   FROM first_rise_breakout_candidate_event e
                   JOIN first_rise_breakout_candidate_state s USING(candidate_event_id)
                   LEFT JOIN first_rise_breakout_paper_trade p
                     ON p.candidate_event_id=e.candidate_event_id AND p.trade_status='OPEN'
                   WHERE e.event_key=%s""", (event_key,),
            )
            return self._state(cursor.fetchone()), created

    def open_trade_state(self, *, candidate_event_id: UUID) -> CandidateState | None:
        """Reload an entered candidate together with its persisted OPEN trade."""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT e.candidate_event_id,e.business_date,e.stock_code,s.state_code,
                          s.peak_price,s.peak_time,s.pullback_low_price,s.pullback_pct,
                          s.last_observed_at,s.last_observed_price,s.entry_event_key,s.state_version,
                          p.entry_signal_time,
                          COALESCE((p.entry_evidence->>'raw_entry_price')::numeric,
                                   p.entry_execution_price / (1 + 2.0/10000.0)),
                          p.entry_execution_price
                   FROM first_rise_breakout_candidate_event e
                   JOIN first_rise_breakout_candidate_state s USING(candidate_event_id)
                   JOIN first_rise_breakout_paper_trade p
                     ON p.candidate_event_id=e.candidate_event_id AND p.trade_status='OPEN'
                   WHERE e.candidate_event_id=%s AND s.state_code='PAPER_ENTERED'""",
                (candidate_event_id,),
            )
            row = cursor.fetchone()
            return self._state(row) if row is not None else None

    def active_states(self, *, business_date: date) -> list[CandidateState]:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT e.candidate_event_id,e.business_date,e.stock_code,s.state_code,
                          s.peak_price,s.peak_time,s.pullback_low_price,s.pullback_pct,
                          s.last_observed_at,s.last_observed_price,s.entry_event_key,s.state_version,
                          p.entry_signal_time,
                          COALESCE((p.entry_evidence->>'raw_entry_price')::numeric,
                                   p.entry_execution_price / (1 + 2.0/10000.0)),
                          p.entry_execution_price
                   FROM first_rise_breakout_candidate_event e
                   JOIN first_rise_breakout_candidate_state s USING(candidate_event_id)
                   LEFT JOIN first_rise_breakout_paper_trade p
                     ON p.candidate_event_id=e.candidate_event_id AND p.trade_status='OPEN'
                   WHERE e.business_date=%s AND s.state_code NOT IN ('PAPER_EXITED','REJECTED','EXPIRED')""",
                (business_date,),
            )
            return [self._state(row) for row in cursor.fetchall()]

    def apply(self, decision: Decision, observation: Observation, *, evidence: dict | None = None) -> CandidateState:
        if not decision.changed:
            return decision.before
        before, after = decision.before, decision.after
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
                              s.last_observed_at,s.last_observed_price,s.entry_event_key,s.state_version,
                              p.entry_signal_time,
                              COALESCE((p.entry_evidence->>'raw_entry_price')::numeric,
                                       p.entry_execution_price / (1 + 2.0/10000.0)),
                              p.entry_execution_price
                       FROM first_rise_breakout_candidate_event e
                       JOIN first_rise_breakout_candidate_state s USING(candidate_event_id)
                       LEFT JOIN first_rise_breakout_paper_trade p
                         ON p.candidate_event_id=e.candidate_event_id AND p.trade_status='OPEN'
                       WHERE e.candidate_event_id=%s""", (before.candidate_event_id,),
                )
                return self._state(cursor.fetchone())
            effective_reason = decision.reason
            effective_evidence = dict(decision.evidence or evidence or {"source": observation.source})
            entry_plan = None
            if decision.create_entry:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_breakout_k_mode_1'))")
                cursor.execute(
                    "SELECT 1 FROM first_rise_breakout_paper_trade WHERE trade_status='OPEN' LIMIT 1"
                )
                if cursor.fetchone() is not None:
                    after = replace(after, state=ResearchState.REJECTED, version=before.version + 1)
                    effective_reason = "OVERLAP_SKIP"
                    effective_evidence["skip_reason"] = "OVERLAP_SKIP"
                else:
                    raw_entry = decision.raw_execution_price or observation.price
                    cursor.execute(
                        """SELECT exit_evidence->>'capital_after'
                           FROM first_rise_breakout_paper_trade
                           WHERE trade_status='CLOSED'
                             AND exit_evidence ? 'capital_after'
                           ORDER BY exit_execution_time DESC,paper_trade_id DESC LIMIT 1"""
                    )
                    capital_row = cursor.fetchone()
                    capital_before = (
                        Decimal(capital_row[0]) if capital_row and capital_row[0]
                        else Decimal("10000000")
                    )
                    from .strategy import FirstRiseBreakoutStrategy
                    plan = FirstRiseBreakoutStrategy.entry_plan(
                        capital=capital_before, raw_entry_price=raw_entry,
                    )
                    one_share_cash = plan["one_share_buy_cash"]
                    quantity = plan["quantity"]
                    if quantity < 1:
                        after = replace(after, state=ResearchState.REJECTED, version=before.version + 1)
                        effective_reason = "NO_CAPITAL"
                        effective_evidence["skip_reason"] = "NO_CAPITAL"
                    else:
                        cash_used = plan["cash_used"]
                        entry_plan = {
                            "raw_entry_price": str(raw_entry),
                            "entry_execution_price": str(
                                FirstRiseBreakoutStrategy.buy_execution_price(raw_entry)
                            ),
                            "buy_fee_bps": "1.46527",
                            "entry_slippage_bps": "2.0",
                            "one_share_buy_cash": str(one_share_cash),
                            "quantity": quantity,
                            "capital_before": str(capital_before),
                            "cash_used": str(cash_used),
                            "cash_remaining": str(plan["cash_remaining"]),
                            "strategy_version": FirstRiseBreakoutStrategy.STRATEGY_VERSION,
                            "capital_policy": "K_MODE",
                            "slot_count": 1,
                        }
                        effective_evidence.update(entry_plan)

            transition_key = (
                f"FRB_TRANSITION|{before.candidate_event_id}|{before.version}|"
                f"{after.state.value}|{observation.observed_at.isoformat()}|{effective_reason}"
            )
            cursor.execute(
                """INSERT INTO first_rise_breakout_state_transition
                   (transition_id,transition_key,candidate_event_id,from_state,to_state,observed_at,
                    observed_price,peak_price,peak_time,pullback_pct,reason_code,evidence)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(transition_key) DO NOTHING""",
                (stable_id(transition_key), transition_key, before.candidate_event_id,
                 before.state.value, after.state.value, observation.observed_at, observation.price,
                 after.peak_price, after.peak_time, after.pullback_pct, effective_reason,
                 Jsonb(effective_evidence)),
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
            if decision.create_entry and entry_plan is not None:
                cursor.execute(
                    """INSERT INTO first_rise_breakout_paper_trade
                       (candidate_event_id,entry_event_key,stock_code,trade_status,
                        entry_signal_time,entry_execution_time,entry_execution_price,entry_evidence)
                       VALUES(%s,%s,%s,'OPEN',%s,%s,%s,%s)
                       ON CONFLICT(entry_event_key) DO NOTHING""",
                    (after.candidate_event_id, after.entry_event_key, after.stock_code,
                     decision.signal_time or observation.observed_at,
                     observation.observed_at, Decimal(entry_plan["entry_execution_price"]),
                     Jsonb(effective_evidence)),
                )
            if decision.create_exit:
                cursor.execute(
                    """SELECT entry_evidence
                       FROM first_rise_breakout_paper_trade
                       WHERE candidate_event_id=%s AND trade_status='OPEN' FOR UPDATE""",
                    (after.candidate_event_id,),
                )
                open_row = cursor.fetchone()
                if open_row is None:
                    raise RuntimeError("open first-rise paper trade disappeared")
                entry_data = open_row[0]
                from .strategy import FirstRiseBreakoutStrategy
                raw_exit = decision.raw_execution_price or observation.price
                quantity = int(entry_data["quantity"])
                exit_plan = FirstRiseBreakoutStrategy.exit_plan(
                    capital_before=Decimal(entry_data["capital_before"]),
                    cash_remaining=Decimal(entry_data["cash_remaining"]),
                    quantity=quantity,
                    raw_exit_price=raw_exit,
                )
                proceeds = exit_plan["sell_proceeds"]
                capital_after = exit_plan["capital_after"]
                realized_pnl = exit_plan["realized_pnl"]
                effective_evidence.update({
                    "raw_exit_price": str(raw_exit),
                    "exit_execution_price": str(
                        FirstRiseBreakoutStrategy.sell_execution_price(raw_exit)
                    ),
                    "quantity": quantity,
                    "sell_proceeds": str(proceeds),
                    "sell_fee_bps": "1.46527",
                    "sell_tax_bps": "20.0",
                    "exit_slippage_bps": "2.0",
                    "realized_pnl": str(realized_pnl),
                    "capital_after": str(capital_after),
                })
                exit_key = f"FRB_EXIT|{after.candidate_event_id}|{observation.observed_at.isoformat()}|{effective_reason}"
                cursor.execute(
                    """UPDATE first_rise_breakout_paper_trade SET trade_status='CLOSED',
                         exit_event_key=%s,exit_signal_time=%s,exit_execution_time=%s,
                         exit_execution_price=%s,exit_reason=%s,exit_evidence=%s,
                         updated_at=CURRENT_TIMESTAMP
                       WHERE candidate_event_id=%s AND trade_status='OPEN'""",
                    (exit_key, decision.signal_time or observation.observed_at,
                     observation.observed_at,
                     FirstRiseBreakoutStrategy.sell_execution_price(raw_exit),
                     effective_reason, Jsonb(effective_evidence),
                     after.candidate_event_id),
                )
            return after
