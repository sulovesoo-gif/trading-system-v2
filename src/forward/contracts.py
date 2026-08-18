"""Forward observation contracts; no candidate is auto-selected or auto-sent."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256


@dataclass(frozen=True)
class ForwardExecutionPath:
    entry_identity: str
    exit_identity: str
    execution_stock_code: str

    @property
    def path_id(self) -> str:
        source = f"{self.entry_identity}|{self.exit_identity}|{self.execution_stock_code}"
        return "FORWARD_" + sha256(source.encode()).hexdigest()[:20]


@dataclass(frozen=True)
class ForwardCandidate:
    candidate_id: str
    strategy_reference: str
    path: ForwardExecutionPath
    signal_stock_code: str
    selection_reason: str
    approved_at: object
    approved_by: str
    active: bool = False


@dataclass(frozen=True)
class ForwardPerformance:
    actual_1share_pnl: Decimal = Decimal("0")
    cost_adjusted_actual_pnl: Decimal = Decimal("0")
    cumulative_simple_return: Decimal = Decimal("0")
    normalized_strategy_return: Decimal = Decimal("0")
    compound_equity: Decimal = Decimal("0")
    mdd: Decimal = Decimal("0")
    wins: int = 0
    losses: int = 0
    trades: int = 0
    peak_equity: Decimal = Decimal("0")
    win_rate: Decimal = Decimal("0")
    profit_factor: Decimal = Decimal("0")
    consecutive_losses: int = 0


class ForwardPerformanceTracker:
    """Records actual one-share P/L separately from normalized comparison P/L.

    Normalized compound equity is analytical only and can never change the
    fixed one-share Forward order quantity.
    """

    def __init__(self, *, normalized_initial_capital: Decimal) -> None:
        if normalized_initial_capital <= 0:
            raise ValueError("normalized initial capital must be positive")
        self._initial = normalized_initial_capital
        self._performance = ForwardPerformance(compound_equity=normalized_initial_capital, peak_equity=normalized_initial_capital)
        self._gross_wins = Decimal("0")
        self._gross_losses = Decimal("0")

    @property
    def performance(self) -> ForwardPerformance:
        return self._performance

    def record_closed_trade(self, *, actual_1share_pnl: Decimal, costs: Decimal, entry_notional: Decimal, normalized_trade_return: Decimal) -> ForwardPerformance:
        if entry_notional <= 0:
            raise ValueError("entry notional must be positive")
        net = actual_1share_pnl - costs
        prior = self._performance
        compound = prior.compound_equity * (Decimal("1") + normalized_trade_return)
        peak = max(prior.peak_equity, compound)
        mdd = min(prior.mdd, (compound - peak) / peak)
        wins = prior.wins + int(net > 0); losses = prior.losses + int(net < 0)
        if net > 0:
            self._gross_wins += net; consecutive_losses = 0
        elif net < 0:
            self._gross_losses += abs(net); consecutive_losses = prior.consecutive_losses + 1
        else:
            consecutive_losses = prior.consecutive_losses
        trades = prior.trades + 1
        self._performance = replace(
            prior, actual_1share_pnl=prior.actual_1share_pnl + actual_1share_pnl,
            cost_adjusted_actual_pnl=prior.cost_adjusted_actual_pnl + net,
            cumulative_simple_return=prior.cumulative_simple_return + net / entry_notional,
            normalized_strategy_return=(compound / self._initial) - Decimal("1"),
            compound_equity=compound, peak_equity=peak, mdd=mdd, wins=wins, losses=losses,
            trades=trades, win_rate=Decimal(wins) / trades,
            profit_factor=(self._gross_wins / self._gross_losses) if self._gross_losses else Decimal("0"),
            consecutive_losses=consecutive_losses,
        )
        return self._performance


class ForwardRegistry:
    """Candidate/path deduplication; actual sends require an external cap gate."""

    def __init__(self) -> None:
        self._candidates: dict[str, ForwardCandidate] = {}
        self._paths: dict[str, list[str]] = {}

    def register(self, candidate: ForwardCandidate) -> ForwardExecutionPath:
        if not candidate.selection_reason or not candidate.approved_by:
            raise ValueError("forward candidate requires human selection audit")
        if candidate.candidate_id in self._candidates:
            raise ValueError("candidate already exists")
        self._candidates[candidate.candidate_id] = candidate
        self._paths.setdefault(candidate.path.path_id, []).append(candidate.candidate_id)
        return candidate.path

    def path_candidates(self, path_id: str) -> tuple[ForwardCandidate, ...]:
        return tuple(self._candidates[item] for item in self._paths.get(path_id, ()))

    @staticmethod
    def one_share_quantity() -> int:
        return 1

    @staticmethod
    def can_send(*, configured_book_cap: Decimal | None, open_acquisition_cost: Decimal, next_cost: Decimal) -> bool:
        return configured_book_cap is not None and open_acquisition_cost + next_cost <= configured_book_cap
