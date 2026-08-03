"""다중 MA 신호를 가상 포지션/성과로 영속화하는 서비스 (주문 없음)."""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from src.analysis.strategy.multi_ma_performance import Portfolio

@dataclass
class PositionRuntime:
    trade_id: int | None = None
    cycle_no: int | None = None
    portfolio: Portfolio = field(default_factory=lambda: Portfolio(Decimal("1000000")))
    applied: set[str] = field(default_factory=set)

class MultiMaPerformanceService:
    def __init__(self, repository, *, initial_capital: Decimal = Decimal("1000000")) -> None:
        self.repository=repository; self.initial_capital=initial_capital; self.runtime: dict[object, PositionRuntime]={}

    def _state(self,key):
        state = self.runtime.get(key)
        if state is not None:
            return state
        state = PositionRuntime(portfolio=Portfolio(self.initial_capital))
        # Restore an already-open cycle after a service restart.  This keeps
        # the next same-observation signal from creating a second OPEN trade.
        if hasattr(self.repository, "get_open_trade"):
            open_trade = self.repository.get_open_trade(key)
            if open_trade is not None:
                trade_id, cycle_no, direction, _time, _price, _ratio, _average = open_trade
                state.trade_id, state.cycle_no = trade_id, cycle_no
                if hasattr(self.repository, "get_trade_legs"):
                    for signal_no, price, ratio in self.repository.get_trade_legs(trade_id):
                        state.portfolio.enter(direction, Decimal(price), Decimal(ratio), signal_no)
                        state.applied.add(signal_no)
        self.runtime[key] = state
        return state

    def process_signal(self,key,*,signal_no,direction,signal_time,price,reason):
        """한 신호를 한 번만 반영하고 필요 시 반대 포지션을 SIGNAL 청산한다."""
        saved = self.repository.save_signal(key,signal_time=signal_time,signal_no=signal_no,direction=direction,price=price,reason=reason)
        if not saved and (not hasattr(self.repository, "signal_is_applied") or self.repository.signal_is_applied(
            key, signal_time=signal_time, signal_no=signal_no, direction=direction,
        )):
            return False
        state=self._state(key); target=Decimal("1") if key.strategy_code != "ACCUMULATED" else Decimal(len(state.applied|{signal_no}))/Decimal("3")
        if state.portfolio.direction not in ("FLAT",direction): self._close(key,state,signal_time,price,"SIGNAL", "MULTIPLE_SIGNALS")
        if state.portfolio.direction=="FLAT":
            state.trade_id,state.cycle_no=self.repository.create_trade(key,direction=direction,entry_time=signal_time,entry_price=price,entry_ratio=target,average_entry_price=price)
            state.portfolio.enter(direction,price,target,signal_no); state.applied={signal_no}
            self.repository.add_trade_leg(trade_id=state.trade_id,signal_no=signal_no,signal_time=signal_time,entry_price=price,entry_ratio=target,notional_amount=self.initial_capital*target)
        elif key.strategy_code=="ACCUMULATED" and signal_no not in state.applied:
            increment=Decimal("1")/Decimal("3"); state.portfolio.enter(direction,price,increment,signal_no); state.applied.add(signal_no)
            self.repository.add_trade_leg(trade_id=state.trade_id,signal_no=signal_no,signal_time=signal_time,entry_price=price,entry_ratio=increment,notional_amount=self.initial_capital*increment)
        self._persist_state(key, state, signal_time)
        return True

    def session_close(self,key,*,exit_time,exit_price):
        state=self._state(key)
        if state.portfolio.direction=="FLAT": return False
        self._close(key,state,exit_time,exit_price,"SESSION_CLOSE","SESSION_END"); return True

    def close_trade_date(self, trade_date, *, exit_time, exit_price) -> int:
        closed = 0
        for key in tuple(self.runtime):
            if key.trade_date == trade_date and self.session_close(key, exit_time=exit_time, exit_price=exit_price):
                closed += 1
        return closed

    def _close(self,key,state,exit_time,exit_price,exit_type,reason):
        pnl,_=state.portfolio.close(exit_price); rate=pnl/self.initial_capital*Decimal("100")
        self.repository.close_trade(trade_id=state.trade_id,exit_time=exit_time,exit_price=exit_price,exit_type=exit_type,exit_reason=reason,profit=pnl,profit_rate=rate)
        self.repository.rebuild_daily_summary(key,initial_capital=self.initial_capital)
        state.trade_id=None; state.cycle_no=None; state.applied=set()
        self._persist_state(key, state, exit_time)

    def _persist_state(self, key, state, processed_time):
        if hasattr(self.repository, "save_state"):
            self.repository.save_state(
                key, last_processed_time=processed_time,
                direction=state.portfolio.direction,
                weight=sum((leg.weight for leg in state.portfolio.legs), Decimal("0")),
                applied_signals=state.applied,
            )
