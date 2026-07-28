"""Synchronous PostgreSQL/TimescaleDB pool configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


class DatabaseConfigurationError(RuntimeError):
    """Database environment configuration is incomplete or unsafe."""


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    name: str
    user: str
    password: str
    pool_min_size: int = 1
    pool_max_size: int = 5

    @classmethod
    def from_environment(cls) -> "DatabaseSettings":
        values = {
            "DB_HOST": os.getenv("DB_HOST", ""), "DB_PORT": os.getenv("DB_PORT", "5432"),
            "DB_NAME": os.getenv("DB_NAME", ""), "DB_USER": os.getenv("DB_USER", ""),
            "DB_PASSWORD": os.getenv("DB_PASSWORD", ""),
        }
        missing = [key for key in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD") if not values[key]]
        if missing:
            raise DatabaseConfigurationError(f"Required database environment variables are missing: {', '.join(missing)}")
        try:
            return cls(
                host=values["DB_HOST"], port=int(values["DB_PORT"]), name=values["DB_NAME"],
                user=values["DB_USER"], password=values["DB_PASSWORD"],
                pool_min_size=int(os.getenv("DB_POOL_MIN_SIZE", "1")),
                pool_max_size=int(os.getenv("DB_POOL_MAX_SIZE", "5")),
            )
        except ValueError as error:
            raise DatabaseConfigurationError("Database port and pool sizes must be integers.") from error

    def connection_kwargs(self) -> dict[str, object]:
        return {"host": self.host, "port": self.port, "dbname": self.name, "user": self.user,
                "password": self.password, "options": "-c TimeZone=Asia/Seoul"}


def configure_connection(connection) -> None:
    """Applied by the pool for every newly-created connection."""
    with connection.cursor() as cursor:
        cursor.execute("SET TIME ZONE 'Asia/Seoul'")
    # psycopg_pool requires a configured connection to be returned idle.
    connection.commit()


def create_connection_pool(settings: DatabaseSettings):
    try:
        from psycopg_pool import ConnectionPool
    except ImportError as error:
        raise DatabaseConfigurationError("psycopg_pool is not installed. Install project requirements first.") from error
    if settings.pool_min_size < 1 or settings.pool_max_size < settings.pool_min_size:
        raise DatabaseConfigurationError("Invalid database connection pool size.")
    pool = ConnectionPool(kwargs=settings.connection_kwargs(), min_size=settings.pool_min_size,
                          max_size=settings.pool_max_size, configure=configure_connection, open=False)
    pool.open(wait=True)
    return pool
