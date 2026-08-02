"""분석 전용 1/3 분할 포지션과 SIGNAL/SESSION_CLOSE 청산 계산."""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal

@dataclass(frozen=True)
class Leg:
    direction: str; price: Decimal; weight: Decimal; signal_type: str
@dataclass
class Portfolio:
    capital: Decimal; direction: str = "FLAT"; legs: list[Leg] = field(default_factory=list); realized: Decimal = Decimal("0")
    def enter(self, direction, price, weight, signal_type):
        self.direction = direction; self.legs.append(Leg(direction, price, weight, signal_type))
    def close(self, price):
        pnl = sum((price-leg.price if leg.direction == "LONG" else leg.price-price) * (self.capital*leg.weight/leg.price) for leg in self.legs)
        self.realized += pnl; closed = tuple(self.legs); self.legs.clear(); self.direction = "FLAT"; return pnl, closed
    def reset(self): self.direction="FLAT"; self.legs.clear()
