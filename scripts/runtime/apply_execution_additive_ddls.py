"""Apply only the additive Step 3/5 DDL files with an explicit operator gate."""

from __future__ import annotations

import os
from pathlib import Path

from src.repository.database import DatabaseSettings


DDL_FILES = ("database/ddl/36_execution_ownership.sql", "database/ddl/37_forward_observation.sql", "database/ddl/38_live_strategy_instance_role.sql")


def main() -> int:
    if os.getenv("APPLY_EXECUTION_ADDITIVE_DDLS") != "YES":
        raise SystemExit("set APPLY_EXECUTION_ADDITIVE_DDLS=YES to apply additive execution DDL")
    import psycopg

    settings = DatabaseSettings.from_environment()
    with psycopg.connect(**settings.connection_kwargs()) as connection, connection.cursor() as cursor:
        for path in DDL_FILES:
            cursor.execute(Path(path).read_text(encoding="utf-8"))
        connection.commit()
    print("APPLIED additive execution DDL 36/37")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
