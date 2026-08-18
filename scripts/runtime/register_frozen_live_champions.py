"""Explicit, disabled-only registry administration for the four frozen LIVE champions.

The operator must set REGISTER_FROZEN_LIVE_CHAMPIONS=YES.  This script never
starts a runtime, changes GLOBAL_TRADE_YN, creates approvals, or invokes a
broker transport.
"""

from __future__ import annotations

import os

from src.live_registry import FROZEN_LIVE_CHAMPIONS, LiveStrategyRegistryRepository


def main() -> int:
    if os.getenv("REGISTER_FROZEN_LIVE_CHAMPIONS") != "YES":
        raise SystemExit("set REGISTER_FROZEN_LIVE_CHAMPIONS=YES for explicit disabled registry creation")
    from src.repository.database import DatabaseSettings, create_connection_pool

    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        repository = LiveStrategyRegistryRepository(pool.connection)
        for champion in FROZEN_LIVE_CHAMPIONS:
            try:
                resolved = repository.register(
                    strategy_id=champion.strategy_id,
                    live_name=champion.live_name,
                    initial_live_capital=champion.initial_live_capital,
                )
            except Exception as exc:
                # Existing rows must be inspected rather than silently repurposed.
                print(f"BLOCKED strategy_id={champion.strategy_id}: {exc}")
                continue
            print(f"CREATED {resolved.strategy_instance_id} strategy_id={resolved.strategy_id} live_yn={resolved.live_yn}")
    finally:
        pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
