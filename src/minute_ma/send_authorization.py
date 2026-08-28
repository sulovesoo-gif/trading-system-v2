from dataclasses import dataclass
from os import environ

MINUTE_MA_LIVE_SEND = "MINUTE_MA_LIVE_SEND"

@dataclass(frozen=True)
class MinuteMaSendProfile:
    profile: str = MINUTE_MA_LIVE_SEND
    enabled: bool = False
    environment_value: str | None = None

    @classmethod
    def from_environment(cls):
        value=environ.get("MINUTE_MA_ACTUAL_SEND")
        return cls(enabled=value == "Y",environment_value=value)

    def require_enabled(self):
        if (self.profile != MINUTE_MA_LIVE_SEND
                or (self.environment_value is not None and self.environment_value not in {"Y","N"})
                or not self.enabled):
            raise PermissionError("MINUTE_MA_SEND_LOCKED")
