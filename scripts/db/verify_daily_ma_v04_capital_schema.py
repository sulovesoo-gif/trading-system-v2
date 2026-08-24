"""Read-only Daily MA V0.4 capital verifier."""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings

VERIFY = ROOT / "database" / "migrations" / "20260824_daily_strategy_ma_v04_capital_verify.sql"

def main() -> int:
    load_dotenv(ROOT / ".env")
    settings = DatabaseSettings.from_environment()
    import psycopg
    values = []
    with psycopg.connect(**settings.connection_kwargs()) as connection, connection.cursor() as cursor:
        for statement in [part.strip() for part in VERIFY.read_text(encoding="utf-8").split(";") if part.strip()]:
            cursor.execute(statement)
            values.append(cursor.fetchone())
    print(json.dumps({"database": settings.name, "checks": values}, default=str))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
