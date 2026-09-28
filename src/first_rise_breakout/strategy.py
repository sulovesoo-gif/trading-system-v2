"""Frozen V1.0 first-rise research decisions; no broker or LIVE concepts."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from .models import CandidateState, Decision, MinuteBar, Observation, ResearchState, TERMINAL_STATES


class FirstRiseBreakoutStrategy:
    STRATEGY_VERSION = "RISE3_REST9_PBMAX4_KMODE1_V1.0"
    ENTRY_START = time(9, 1)
    ENTRY_CUTOFF = time(10, 0)
    SESSION_CLOSE = time(15, 30)
    MIN_RISE = Decimal("0.03")
    MIN_PREVIOUS_CLOSE = Decimal("1000")
    MIN_SESSION_VOLUME = 1_000_000
    MIN_SESSION_AMOUNT = Decimal("1000000000")
    CORPORATE_ACTION_HIGH = Decimal("1.305")
    CORPORATE_ACTION_LOW = Decimal("0.695")
    MIN_PULLBACK = Decimal("0.005")
    MAX_PULLBACK = Decimal("0.04")
    MIN_PEAK_AGE = timedelta(minutes=9)
    PROFIT_ARM_NET = Decimal("0.01")
    BUY_FEE = Decimal("1.46527") / Decimal("10000")
    SELL_FEE = Decimal("1.46527") / Decimal("10000")
    SELL_TAX = Decimal("20") / Decimal("10000")
    SLIPPAGE = Decimal("2") / Decimal("10000")

    @classmethod
    def buy_execution_price(cls, raw_price: Decimal) -> Decimal:
        return raw_price * (Decimal("1") + cls.SLIPPAGE)

    @classmethod
    def one_share_buy_cash(cls, raw_price: Decimal) -> Decimal:
        return cls.buy_execution_price(raw_price) * (Decimal("1") + cls.BUY_FEE)

    @classmethod
    def sell_execution_price(cls, raw_price: Decimal) -> Decimal:
        return raw_price * (Decimal("1") - cls.SLIPPAGE)

    @classmethod
    def one_share_sell_cash(cls, raw_price: Decimal) -> Decimal:
        return cls.sell_execution_price(raw_price) * (
            Decimal("1") - cls.SELL_FEE - cls.SELL_TAX
        )

    @classmethod
    def net_return(cls, *, raw_entry_price: Decimal, raw_exit_price: Decimal) -> Decimal:
        return cls.one_share_sell_cash(raw_exit_price) / cls.one_share_buy_cash(raw_entry_price) - 1

    @classmethod
    def entry_plan(cls, *, capital: Decimal, raw_entry_price: Decimal) -> dict:
        one_share_cash = cls.one_share_buy_cash(raw_entry_price)
        quantity = int(capital // one_share_cash)
        cash_used = one_share_cash * quantity
        return {
            "quantity": quantity,
            "one_share_buy_cash": one_share_cash,
            "cash_used": cash_used,
            "cash_remaining": capital - cash_used,
        }

    @classmethod
    def exit_plan(
        cls, *, capital_before: Decimal, cash_remaining: Decimal,
        quantity: int, raw_exit_price: Decimal,
    ) -> dict:
        proceeds = cls.one_share_sell_cash(raw_exit_price) * quantity
        capital_after = cash_remaining + proceeds
        return {
            "sell_proceeds": proceeds,
            "realized_pnl": capital_after - capital_before,
            "capital_after": capital_after,
        }

    def seed_peak(self, state: CandidateState, *, peak_price: Decimal, peak_time: datetime) -> Decision:
        if peak_price <= 0:
            raise ValueError("peak_price must be positive")
        after = state.evolve(
            state=ResearchState.TRACKING,
            peak_price=peak_price,
            peak_time=peak_time,
            pullback_low_price=None,
            pullback_pct=None,
            last_observed_at=peak_time,
            last_observed_price=peak_price,
        )
        return Decision(state, after, "RECORD_HIGH_SEEDED")

    def observe_bar(
        self, state: CandidateState, bar: MinuteBar, *, previous_close: Decimal,
        allow_entry: bool = True, bootstrap: bool = False,
        session_volume: int | None = None,
        session_amount: Decimal | None = None,
    ) -> Decision:
        """Apply one completed KRX one-minute bar in chronological order."""
        if min(bar.open_price, bar.high_price, bar.low_price, bar.close_price) <= 0:
            raise ValueError("minute bar prices must be positive")
        if previous_close <= 0:
            raise ValueError("previous_close must be positive")
        if state.state in TERMINAL_STATES or state.state == ResearchState.PAPER_ENTERED:
            return Decision(state, state, "STATE_NOT_ENTRY_ELIGIBLE")
        if bar.bar_time.date() != state.business_date:
            return Decision(state, state, "DIFFERENT_BUSINESS_DATE")
        if session_volume is not None and previous_close < self.MIN_PREVIOUS_CLOSE:
            after = state.evolve(state=ResearchState.REJECTED, last_observed_at=bar.bar_time)
            return Decision(state, after, "PREVIOUS_CLOSE_BELOW_1000")
        if (
            bar.high_price > previous_close * self.CORPORATE_ACTION_HIGH
            or bar.low_price < previous_close * self.CORPORATE_ACTION_LOW
        ):
            after = state.evolve(state=ResearchState.REJECTED, last_observed_at=bar.bar_time)
            return Decision(state, after, "CORPORATE_ACTION_RANGE_REJECTED")
        if bar.bar_time.time() < self.ENTRY_START:
            return Decision(state, state, "BEFORE_ENTRY_WINDOW")
        if bar.bar_time.time() >= self.ENTRY_CUTOFF:
            return Decision(state, state, "AFTER_ENTRY_WINDOW")
        if state.peak_price is None or state.peak_time is None:
            return self.seed_peak(state, peak_price=bar.high_price, peak_time=bar.bar_time)

        peak = state.peak_price
        peak_time = state.peak_time
        has_valid_pullback = (
            state.pullback_pct is not None
            and self.MIN_PULLBACK <= state.pullback_pct <= self.MAX_PULLBACK
        )
        if bar.high_price > peak:
            rise = peak / previous_close - Decimal("1")
            rest = bar.bar_time - peak_time
            decision_evidence = self._bar_evidence(
                bar, bootstrap=bootstrap, previous_close=previous_close,
            )
            if has_valid_pullback and rest >= self.MIN_PEAK_AGE and rise >= self.MIN_RISE:
                raw_entry = max(bar.open_price, peak)
                historical_liquidity_threshold_met = (
                    None if session_volume is None or session_amount is None else
                    session_volume is not None
                    and session_amount is not None
                    and session_volume >= self.MIN_SESSION_VOLUME
                    and session_amount >= self.MIN_SESSION_AMOUNT
                )
                decision_evidence = self._bar_evidence(
                    bar,
                    bootstrap=bootstrap,
                    previous_close=previous_close,
                    peak_time=peak_time,
                    prior_high=peak,
                    prior_high_rise_pct=rise * Decimal("100"),
                    pullback_low=state.pullback_low_price,
                    pullback_pct=state.pullback_pct * Decimal("100"),
                    rest_minutes=Decimal(str(rest.total_seconds() / 60)),
                    raw_entry_price=raw_entry,
                    breakout_time=bar.bar_time,
                    entry_signal_time=bar.bar_time,
                    entry_execution_time=bar.bar_time,
                    session_volume=session_volume,
                    session_amount=session_amount,
                    historical_liquidity_threshold_met=historical_liquidity_threshold_met,
                    historical_liquidity_entry_gate=False,
                )
                if allow_entry:
                    key = (
                        f"FRB|{state.business_date.isoformat()}|{state.stock_code}|"
                        f"{peak_time.isoformat()}|{bar.bar_time.isoformat()}"
                    )
                    after = state.evolve(
                        state=ResearchState.PAPER_ENTERED,
                        last_observed_at=bar.bar_time,
                        last_observed_price=bar.close_price,
                        entry_event_key=key,
                        entry_signal_time=bar.bar_time,
                        raw_entry_price=raw_entry,
                        entry_execution_price=self.buy_execution_price(raw_entry),
                    )
                    return Decision(
                        state, after, "SAME_PEAK_REBREAK_CONFIRMED", create_entry=True,
                        signal_time=bar.bar_time, raw_execution_price=raw_entry,
                        evidence=decision_evidence,
                    )
                reason = "MISSED_BEFORE_DISCOVERY"
                decision_evidence["missed_before_discovery"] = True
            else:
                reason = "NEW_RECORD_HIGH_RESET"
            after = state.evolve(
                state=ResearchState.TRACKING,
                peak_price=bar.high_price,
                peak_time=bar.bar_time,
                pullback_low_price=None,
                pullback_pct=None,
                last_observed_at=bar.bar_time,
                last_observed_price=bar.close_price,
            )
            return Decision(
                state, after, reason,
                evidence=decision_evidence,
            )

        low = min(value for value in (state.pullback_low_price, bar.low_price) if value is not None)
        pullback = (peak - low) / peak
        if pullback > self.MAX_PULLBACK:
            after = state.evolve(
                state=ResearchState.TRACKING,
                pullback_low_price=low,
                pullback_pct=pullback,
                last_observed_at=bar.bar_time,
                last_observed_price=bar.close_price,
            )
            return Decision(
                state, after, "PULLBACK_OVER_4_STRUCTURE_INVALIDATED",
                evidence=self._bar_evidence(bar, bootstrap=bootstrap, previous_close=previous_close),
            )
        if pullback >= self.MIN_PULLBACK:
            after = state.evolve(
                state=(ResearchState.PULLBACK if state.state == ResearchState.TRACKING
                       else ResearchState.WAIT_REBREAK),
                pullback_low_price=low,
                pullback_pct=pullback,
                last_observed_at=bar.bar_time,
                last_observed_price=bar.close_price,
            )
            return Decision(
                state, after,
                "VALID_PULLBACK_OBSERVED" if state.state == ResearchState.TRACKING
                else "WAITING_SAME_PEAK_REBREAK",
                evidence=self._bar_evidence(bar, bootstrap=bootstrap, previous_close=previous_close),
            )
        after = state.evolve(
            pullback_low_price=low,
            pullback_pct=pullback,
            last_observed_at=bar.bar_time,
            last_observed_price=bar.close_price,
        )
        return Decision(
            state, after, "TRACKING_WITHIN_PULLBACK_THRESHOLD",
            evidence=self._bar_evidence(bar, bootstrap=bootstrap, previous_close=previous_close),
        )

    def observe(self, state: CandidateState, observation: Observation) -> Decision:
        """Ticks are auxiliary observations and never confirm the frozen signal."""
        if observation.price <= 0:
            raise ValueError("observation price must be positive")
        return Decision(state, state, "TICK_AUXILIARY_ONLY")

    def exit_from_completed_bars(
        self, state: CandidateState, bars: list[MinuteBar], *, session_ended: bool = False,
    ) -> Decision:
        if state.state != ResearchState.PAPER_ENTERED or state.raw_entry_price is None:
            return Decision(state, state, "NO_OPEN_PAPER_TRADE")
        ordered = sorted(
            (bar for bar in bars if bar.bar_time.date() == state.business_date),
            key=lambda bar: bar.bar_time,
        )
        entry_time = state.entry_signal_time or state.last_observed_at
        if entry_time is None:
            return Decision(state, state, "OPEN_TRADE_ENTRY_TIME_UNAVAILABLE")

        stop = next(
            (bar for bar in ordered
             if bar.bar_time > entry_time and bar.low_price < state.raw_entry_price),
            None,
        )
        profit_signal_time = self._profit_signal_time(
            ordered, entry_time=entry_time, raw_entry_price=state.raw_entry_price,
        )
        profit_exec = None
        if profit_signal_time is not None:
            profit_exec = next((bar for bar in ordered if bar.bar_time >= profit_signal_time), None)

        if stop is not None and (profit_exec is None or stop.bar_time <= profit_exec.bar_time):
            raw_exit = min(stop.open_price, state.raw_entry_price)
            return self._exit_decision(
                state, signal_time=stop.bar_time, execution_bar=stop,
                raw_exit_price=raw_exit, reason="STOP_ENTRY_BREAK",
            )
        if profit_exec is not None:
            return self._exit_decision(
                state, signal_time=profit_signal_time, execution_bar=profit_exec,
                raw_exit_price=profit_exec.open_price, reason="BOOK_TENKAN_PROFIT",
            )
        if session_ended and ordered:
            eod = max(ordered, key=lambda bar: bar.bar_time)
            return self._exit_decision(
                state, signal_time=eod.bar_time, execution_bar=eod,
                raw_exit_price=eod.close_price, reason="SESSION_CLOSE",
            )
        return Decision(state, state, "OPEN_POSITION_CONTINUES")

    def expire(self, state: CandidateState, *, at: datetime) -> Decision:
        if state.state in TERMINAL_STATES or state.state == ResearchState.PAPER_ENTERED:
            return Decision(state, state, "STATE_NOT_EXPIRABLE")
        return Decision(
            state, state.evolve(state=ResearchState.EXPIRED, last_observed_at=at),
            "ENTRY_CUTOFF_EXPIRED",
        )

    def exit(self, state: CandidateState, observation: Observation, *, reason: str) -> Decision:
        if state.state != ResearchState.PAPER_ENTERED:
            return Decision(state, state, "NO_OPEN_PAPER_TRADE")
        after = state.evolve(
            state=ResearchState.PAPER_EXITED,
            last_observed_at=observation.observed_at,
            last_observed_price=observation.price,
        )
        return Decision(
            state, after, reason, create_exit=True,
            signal_time=observation.observed_at,
            raw_execution_price=observation.price,
            evidence={"source": observation.source},
        )

    def _profit_signal_time(
        self, bars: list[MinuteBar], *, entry_time: datetime, raw_entry_price: Decimal,
    ) -> datetime | None:
        grouped: dict[datetime, list[MinuteBar]] = defaultdict(list)
        session_open = datetime.combine(entry_time.date(), time(9, 0))
        for bar in bars:
            if not (time(9, 0) <= bar.bar_time.time() <= self.SESSION_CLOSE):
                continue
            offset = int((bar.bar_time - session_open).total_seconds() // 180)
            grouped[session_open + timedelta(minutes=offset * 3)].append(bar)

        completed = []
        for start in sorted(grouped):
            rows = sorted(grouped[start], key=lambda bar: bar.bar_time)
            expected = [start + timedelta(minutes=i) for i in range(3)]
            if [bar.bar_time for bar in rows] != expected:
                continue
            completed.append({
                "start": start,
                "complete": start + timedelta(minutes=3),
                "open": rows[0].open_price,
                "high": max(row.high_price for row in rows),
                "low": min(row.low_price for row in rows),
                "close": rows[-1].close_price,
            })

        running_best: Decimal | None = None
        for index, bar3 in enumerate(completed):
            if bar3["complete"] <= entry_time:
                continue
            net = self.net_return(raw_entry_price=raw_entry_price, raw_exit_price=bar3["close"])
            running_best = net if running_best is None else max(running_best, net)
            if index < 8:
                continue
            window = completed[index - 8:index + 1]
            tenkan = (
                max(row["high"] for row in window)
                + min(row["low"] for row in window)
            ) / Decimal("2")
            if (
                running_best >= self.PROFIT_ARM_NET
                and bar3["close"] < bar3["open"]
                and bar3["close"] < tenkan
            ):
                return bar3["complete"]
        return None

    def _exit_decision(
        self, state: CandidateState, *, signal_time: datetime,
        execution_bar: MinuteBar, raw_exit_price: Decimal, reason: str,
    ) -> Decision:
        after = state.evolve(
            state=ResearchState.PAPER_EXITED,
            last_observed_at=execution_bar.bar_time,
            last_observed_price=execution_bar.close_price,
        )
        evidence = self._bar_evidence(
            execution_bar, raw_exit_price=raw_exit_price,
            exit_reason=reason, signal_time=signal_time,
            exit_execution_time=execution_bar.bar_time,
        )
        return Decision(
            state, after, reason, create_exit=True, signal_time=signal_time,
            raw_execution_price=raw_exit_price, evidence=evidence,
        )

    @staticmethod
    def _bar_evidence(bar: MinuteBar, **extra) -> dict:
        evidence = {
            "source": bar.source,
            "bar_time": bar.bar_time.isoformat(),
            "open": str(bar.open_price),
            "high": str(bar.high_price),
            "low": str(bar.low_price),
            "close": str(bar.close_price),
            "volume": bar.volume,
            "accumulated_amount": (
                str(bar.accumulated_amount) if bar.accumulated_amount is not None else None
            ),
        }
        for key, value in extra.items():
            if isinstance(value, Decimal):
                value = str(value)
            elif isinstance(value, datetime):
                value = value.isoformat()
            evidence[key] = value
        return evidence
