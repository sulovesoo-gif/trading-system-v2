"""Daily MA's send authorization is independent from the 7C smoke permit."""

from __future__ import annotations

from dataclasses import dataclass
from os import environ


DAILY_MA_LIVE_SEND = "DAILY_MA_LIVE_SEND"


@dataclass(frozen=True)
class DailyMaSendProfile:
    profile: str = DAILY_MA_LIVE_SEND
    enabled: bool = False

    @classmethod
    def from_environment(cls) -> "DailyMaSendProfile":
        # Missing or malformed values are deliberately indistinguishable from OFF.
        return cls(enabled=environ.get("DAILY_MA_ACTUAL_SEND", "N") == "Y")

    def require_enabled(self) -> None:
        if self.profile != DAILY_MA_LIVE_SEND or not self.enabled:
            raise PermissionError("DAILY_MA_SEND_LOCKED")
