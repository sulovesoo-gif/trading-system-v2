"""Fail-closed Forward Book cap contract, separate from LIVE capital/reserve."""
from __future__ import annotations
import os
from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class ForwardBookStatus:
 cap: Decimal|None; gross_exposure: Decimal
 @property
 def remaining_capacity(self): return None if self.cap is None else max(Decimal('0'),self.cap-self.gross_exposure)
 @property
 def actual_send_allowed(self): return self.cap is not None and self.gross_exposure <= self.cap
def forward_book_cap_from_environment():
 value=os.getenv('FORWARD_BOOK_CAP_KRW','').strip()
 return Decimal(value) if value else None
