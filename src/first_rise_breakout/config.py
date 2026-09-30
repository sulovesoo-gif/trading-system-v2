"""Typed PAPER/research hours; LIVE hours are reserved configuration only."""

from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True)
class FirstRiseRuntimeConfig:
    paper_entry_start: time
    paper_entry_cutoff: time
    live_entry_start: time
    live_entry_cutoff: time

    @classmethod
    def from_row(cls, row):
        if row is None or row[0] != "Y":
            raise ValueError("FIRST_RISE_RUNTIME/DEFAULT missing or inactive")
        try:
            values = [time.fromisoformat(value) for value in row[1:5]]
            if len(values) != 4 or any(value.tzinfo is not None for value in values):
                raise ValueError("four local times required")
            if not (values[0] < values[1] <= time(15, 30)) or not values[2] < values[3]:
                raise ValueError("invalid entry window")
            return cls(*values)
        except (TypeError, ValueError) as error:
            raise ValueError("FIRST_RISE_RUNTIME/DEFAULT invalid time settings") from error

    def evidence(self):
        return {
            "config_source": "FIRST_RISE_RUNTIME/DEFAULT",
            "PAPER_ENTRY_START": self.paper_entry_start.isoformat(),
            "PAPER_ENTRY_CUTOFF": self.paper_entry_cutoff.isoformat(),
            "runtime_contract": "CONFIGURED_PAPER_WINDOW_V1",
        }
