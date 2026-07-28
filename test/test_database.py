from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.repository.database import DatabaseConfigurationError, DatabaseSettings, configure_connection


class Cursor:
    def __init__(self):
        self.queries = []

    def execute(self, query):
        self.queries.append(query)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Connection:
    def __init__(self):
        self.cursor_value = Cursor()
        self.commits = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1


class DatabaseSettingsTest(unittest.TestCase):
    def test_connection_kwargs_force_kst_for_every_connection(self):
        settings = DatabaseSettings("localhost", 5432, "trading_system_v2_test", "user", "password")
        self.assertEqual(settings.connection_kwargs()["options"], "-c TimeZone=Asia/Seoul")
        connection = Connection()
        configure_connection(connection)
        self.assertEqual(connection.cursor_value.queries, ["SET TIME ZONE 'Asia/Seoul'"])
        self.assertEqual(connection.commits, 1)

    def test_missing_environment_is_rejected_without_exposing_password(self):
        with patch.dict(os.environ, {"DB_HOST": "", "DB_NAME": "", "DB_USER": "", "DB_PASSWORD": "TOP_SECRET"}, clear=False):
            with self.assertRaises(DatabaseConfigurationError) as context:
                DatabaseSettings.from_environment()
        self.assertNotIn("TOP_SECRET", str(context.exception))
