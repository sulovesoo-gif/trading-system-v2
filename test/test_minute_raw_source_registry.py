import unittest

from src.collector.runtime.minute_raw_source_registry import MinuteRawSourceRegistry


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""

    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, sql): self.sql = sql
    def fetchall(self): return self.rows


class Connection:
    def __init__(self, cursor): self._cursor = cursor
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def cursor(self): return self._cursor


class Pool:
    def __init__(self, rows): self.cursor = Cursor(rows)
    def connection(self): return Connection(self.cursor)


class MinuteRawSourceRegistryTest(unittest.TestCase):
    def test_returns_only_enabled_common_code_minute_targets(self):
        rows = tuple((code,) for code in (
            "000660", "005930", "0193T0", "0197X0", "0193W0", "0193L0"))
        registry = MinuteRawSourceRegistry(Pool(rows))

        self.assertEqual(registry.stock_codes(), tuple(code for (code,) in rows))
        self.assertIn("JOIN common_code_group", registry.pool.cursor.sql)
        self.assertIn("c.attr2 = 'Y'", registry.pool.cursor.sql)

    def test_empty_stock_code_fails_closed(self):
        registry = MinuteRawSourceRegistry(Pool((("",),)))
        with self.assertRaises(ValueError):
            registry.stock_codes()


if __name__ == "__main__":
    unittest.main()
