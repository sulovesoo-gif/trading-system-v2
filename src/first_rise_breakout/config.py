"""Daily FIRST_RISE settings. Slot amounts are NOT total capital allocations."""

from dataclasses import dataclass
from datetime import time
import re


@dataclass(frozen=True)
class FirstRiseRuntimeConfig:
    paper_entry_start: time
    paper_entry_cutoff: time
    live_entry_start: time
    live_entry_cutoff: time
    start_slot_amount: int
    slot_step_amount: int
    max_slot_amount: int

    @classmethod
    def from_row(cls, row):
        if not row or row[0] != "Y":
            raise ValueError("FIRST_RISE_RUNTIME/DEFAULT missing or inactive")
        try:
            values = [time.fromisoformat(value) for value in row[1:5]]
            if len(values) != 4 or any(value.tzinfo is not None for value in values):
                raise ValueError("four local times required")
            if not (values[0] < values[1] <= time(15, 30)) or not (values[2] < values[3] <= time(15, 30)):
                raise ValueError("invalid entry window")
            if len(row) != 8:
                raise ValueError("attr1 through attr7 required")
            amounts = []
            for value in row[5:8]:
                if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value.strip()):
                    raise ValueError("slot amount must be integer KRW")
                amount = int(value.strip())
                if amount <= 0:
                    raise ValueError("slot amount must be positive")
                amounts.append(amount)
            if amounts[2] < amounts[0]:
                raise ValueError("MAX_SLOT_AMOUNT below START_SLOT_AMOUNT")
            return cls(*values, *amounts)
        except (TypeError, ValueError) as error:
            raise ValueError("FIRST_RISE_RUNTIME/DEFAULT invalid time/slot settings") from error

    def evidence(self):
        return {
            "config_source": "FIRST_RISE_RUNTIME/DEFAULT",
            "PAPER_ENTRY_START": self.paper_entry_start.isoformat(),
            "PAPER_ENTRY_CUTOFF": self.paper_entry_cutoff.isoformat(),
            "LIVE_ENTRY_START": self.live_entry_start.isoformat(),
            "LIVE_ENTRY_CUTOFF": self.live_entry_cutoff.isoformat(),
            "START_SLOT_AMOUNT": self.start_slot_amount,
            "SLOT_STEP_AMOUNT": self.slot_step_amount,
            "MAX_SLOT_AMOUNT": self.max_slot_amount,
            "runtime_contract": "FIRST_RISE_CONFIG_ATTR1_7_V2",
        }
