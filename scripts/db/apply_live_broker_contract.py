"""Apply DDL 31 shared broker ledger to TEST only with an explicit gate."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.repository.database import DatabaseSettings


DDL = ROOT / "database" / "ddl" / "31_live_broker_contract.sql"


def main() -> int:
    load_dotenv(ROOT / ".env")
    if os.getenv("APPLY_LIVE_BROKER_CONTRACT") != "YES":
        raise SystemExit("set APPLY_LIVE_BROKER_CONTRACT=YES")
    settings = DatabaseSettings.from_environment()
    if settings.name != "trading_system_v2_test":
        raise SystemExit("DDL 31 apply is restricted to trading_system_v2_test")
    import psycopg

    with psycopg.connect(**settings.connection_kwargs()) as connection, connection.cursor() as cursor:
        cursor.execute(DDL.read_text(encoding="utf-8"))
        connection.commit()
    print("APPLIED DDL 31 shared broker ledger to TEST")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
