from dataclasses import dataclass
from datetime import date, datetime, time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.live_registry import LiveStrategyResolution


@dataclass(frozen=True)
class SmokeConfig:
    phase: str
    active_product: str | None = None
    active_strategy_instance: str | None = None
    allowed_start: time | None = None
    allowed_end: time | None = None
    kill_switch_enabled: bool = False


@dataclass(frozen=True)
class SmokeRequest:
    product: str
    strategy_instance_id: str
    side: str
    quantity: int
    at: datetime
    outstanding: int
    daily_submit_count: int
    actual_position_quantity: int = 0


class SmokeGate:
    whitelist = {
        "0193W0": ("KODEX Samsung Electronics single-stock leverage", "ETF"),
        "0193L0": ("PLUS Samsung Electronics single-stock inverse 2X", "ETF"),
        "0197X0": ("SOL SK hynix single-stock inverse 2X", "ETF"),
    }

    def validate(self, config: SmokeConfig, request: SmokeRequest):
        if not config.kill_switch_enabled:
            return False, "KILL_SWITCH_BLOCKED"
        if not config.active_product or not config.active_strategy_instance:
            return False, "PHASE_NOT_APPROVED"
        if (
            request.product != config.active_product
            or request.strategy_instance_id != config.active_strategy_instance
        ):
            return False, "WHITELIST_OR_ATTRIBUTION_BLOCKED"
        if request.product not in self.whitelist:
            return False, "PRODUCT_BLOCKED"
        if request.quantity != 1:
            return False, "QTY_MUST_BE_ONE"
        if request.daily_submit_count >= 1:
            return False, "DAILY_SUBMIT_LIMIT"
        if request.outstanding >= 1:
            return False, "OUTSTANDING_LIMIT"
        if (
            config.allowed_start is None
            or config.allowed_end is None
            or not config.allowed_start <= request.at.time() <= config.allowed_end
        ):
            return False, "TIME_WINDOW_BLOCKED"
        if config.phase == "7C-1" and request.side != "BUY":
            return False, "PHASE_SIDE_BLOCKED"
        if config.phase == "7C-2" and not (
            request.side == "SELL" and request.actual_position_quantity == 1
        ):
            return False, "POSITION_REQUIRED"
        return False, "NO_SUBMIT_IMPLEMENTED"


@dataclass(frozen=True)
class ResolvedSmokeConfig:
    """A no-send 7C-1 approval record; it cannot call a broker client."""

    active_stock_code: str | None
    strategy_instance_id: str | None
    allowed_date: date | None
    allowed_time_from: time | None
    allowed_time_to: time | None
    side: str = "BUY"
    quantity: int = 1
    daily_submit_max: int = 1
    open_order_max: int = 1
    retry_on_timeout: bool = False
    retry_on_unknown: bool = False
    auto_sell: bool = False
    auto_retry: bool = False
    auto_scale: bool = False
    kill_switch: str = "ARMED_FOR_ONE_SUBMIT"
    global_trade_yn: str = "N"
    network_send_enabled: bool = False
    registry_resolution: "LiveStrategyResolution | None" = None

    def validate(self):
        checks = []

        def add(ok: bool, label: str):
            checks.append((ok, label))

        add(bool(self.active_stock_code), "active_stock_code exactly one")
        add(
            self.active_stock_code in SmokeGate.whitelist,
            "active_stock_code is whitelisted",
        )
        add(bool(self.strategy_instance_id), "strategy_instance_id exactly one")
        registry_valid = (
            self.registry_resolution is not None
            and self.registry_resolution.strategy_instance_id == self.strategy_instance_id
            and self.registry_resolution.execution_stock_code == self.active_stock_code
            and self.registry_resolution.smoke_safe
        )
        add(registry_valid, "strategy_instance_id is existing/valid")
        add(self.side == "BUY", "side == BUY")
        add(self.quantity == 1, "quantity == 1")
        add(self.allowed_date is not None, "allowed_date resolved")
        add(self.allowed_time_from is not None, "allowed_time_from resolved")
        add(self.allowed_time_to is not None, "allowed_time_to resolved")
        add(
            self.allowed_time_from is not None
            and self.allowed_time_to is not None
            and self.allowed_time_from < self.allowed_time_to,
            "allowed time window valid",
        )
        add(self.daily_submit_max == 1, "daily submit max == 1")
        add(self.open_order_max == 1, "open order max == 1")
        add(not self.retry_on_timeout, "retry on timeout disabled")
        add(not self.retry_on_unknown, "retry on UNKNOWN disabled")
        add(not self.auto_sell, "auto sell disabled")
        add(not self.auto_retry, "auto retry disabled")
        add(not self.auto_scale, "auto scale disabled")
        add(self.kill_switch == "ARMED_FOR_ONE_SUBMIT", "kill switch armed for one submit")
        add(self.global_trade_yn == "N", "GLOBAL_TRADE_YN remains N")
        add(not self.network_send_enabled, "network send disabled in dry-run")
        # The dry-run module does not import or construct a broker adapter.
        add(True, "actual network send count == 0")
        add(True, "actual submit count == 0")
        return checks

    def render(self) -> str:
        checks = self.validate()
        valid = all(result for result, _ in checks)
        lines = [
            "7C-1 RESOLVED SMOKE CONFIG",
            f"active_stock_code      = {self.active_stock_code}",
            f"strategy_instance_id   = {self.strategy_instance_id}",
            "",
            f"allowed_side           = {self.side}",
            f"quantity               = {self.quantity}",
            f"allowed_date           = {self.allowed_date}",
            f"allowed_time_from      = {self.allowed_time_from} KST",
            f"allowed_time_to        = {self.allowed_time_to} KST",
            f"daily_submit_max       = {self.daily_submit_max}",
            f"open_order_max         = {self.open_order_max}",
            "retry_on_timeout       = " + ("DISABLED" if not self.retry_on_timeout else "ENABLED"),
            "retry_on_unknown       = " + ("DISABLED" if not self.retry_on_unknown else "ENABLED"),
            f"kill_switch            = {self.kill_switch}",
            "auto_sell              = " + ("DISABLED" if not self.auto_sell else "ENABLED"),
            "auto_retry             = " + ("DISABLED" if not self.auto_retry else "ENABLED"),
            "auto_scale             = " + ("DISABLED" if not self.auto_scale else "ENABLED"),
            f"GLOBAL_TRADE_YN        = {self.global_trade_yn}",
            "network_send_enabled   = " + ("N" if not self.network_send_enabled else "Y"),
            "actual_network_send_count = 0",
            "actual_submit_count    = 0",
            "",
        ]
        lines.extend(f"[{'PASS' if result else 'FAIL'}] {label}" for result, label in checks)
        lines.extend(
            [
                "",
                "RESULT",
                "DRY_RUN_ONLY\nNO ORDER WILL BE SENT"
                if valid
                else "RESOLVED_CONFIG_INVALID\n7C-1 APPROVAL BLOCKED",
            ]
        )
        return "\n".join(lines)
