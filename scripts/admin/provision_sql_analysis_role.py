"""Idempotently provision the LOGIN role used by the mobile SQL runner.

Run with the normal application DB credentials plus ANALYSIS_DB_USER and
ANALYSIS_DB_PASSWORD.  The role can read permanent public objects and create or
mutate TEMP objects only; it cannot write permanent application data.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import psycopg
from psycopg import sql
from dotenv import load_dotenv

from src.repository.database import DatabaseSettings


def main() -> int:
    load_dotenv(ROOT / ".env")
    settings = DatabaseSettings.from_environment()
    role = os.environ.get("ANALYSIS_DB_USER", "").strip()
    password = os.environ.get("ANALYSIS_DB_PASSWORD", "")
    if not role or not password:
        raise RuntimeError("ANALYSIS_DB_USER and ANALYSIS_DB_PASSWORD are required")
    if role == settings.user:
        raise RuntimeError("analysis role must differ from the application role")
    with psycopg.connect(**settings.connection_kwargs(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS").format(
                    sql.Identifier(role), sql.Literal(password)))
            else:
                cur.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS").format(
                    sql.Identifier(role), sql.Literal(password)))
            cur.execute(sql.SQL("GRANT CONNECT, TEMPORARY ON DATABASE {} TO {}").format(
                sql.Identifier(settings.name), sql.Identifier(role)))
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            cur.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(sql.Identifier(role)))
            cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(role)))
            cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {}").format(sql.Identifier(role)))
            defaults = {
                "default_transaction_read_only": "on",
                "statement_timeout": os.getenv("SQL_ANALYSIS_STATEMENT_TIMEOUT", "45min"),
                "lock_timeout": os.getenv("SQL_ANALYSIS_LOCK_TIMEOUT", "5s"),
                "idle_in_transaction_session_timeout": os.getenv("SQL_ANALYSIS_IDLE_TX_TIMEOUT", "5min"),
                "work_mem": os.getenv("SQL_ANALYSIS_WORK_MEM", "32MB"),
                "temp_file_limit": os.getenv("SQL_ANALYSIS_TEMP_FILE_LIMIT", "2GB"),
            }
            for name, value in defaults.items():
                cur.execute(sql.SQL("ALTER ROLE {} IN DATABASE {} SET {} TO {}").format(
                    sql.Identifier(role), sql.Identifier(settings.name), sql.Identifier(name), sql.Literal(value)))
            cur.execute("""SELECT r.rolsuper,r.rolcreatedb,r.rolcreaterole,r.rolinherit,r.rolreplication,r.rolbypassrls,
                                  EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid)
                             FROM pg_roles r WHERE r.rolname=%s""", (role,))
            flags = cur.fetchone()
            if flags != (False, False, False, False, False, False, False):
                raise RuntimeError("analysis role privilege invariant failed")
    print("SQL analysis role provisioned with permanent SELECT + TEMP-only write privileges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
