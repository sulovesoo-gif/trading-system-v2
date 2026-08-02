from __future__ import annotations

import re
import unittest
from pathlib import Path

from src.repository.raw_specs import RAW_SPECS, RawTable


ROOT = Path(__file__).parents[1]


def ddl_columns(content: str) -> tuple[str, ...]:
    definition = re.search(r"CREATE TABLE (?:IF NOT EXISTS )?\w+\s*\((.*?)\n\);", content, re.DOTALL).group(1)
    return tuple(match.group(1) for line in definition.splitlines() if (match := re.match(r"\s{4}([a-z][a-z0-9_]*)\s+(?:TIMESTAMP|DATE|VARCHAR|CHAR|BIGINT|SMALLINT|INTEGER|NUMERIC|JSONB)", line)))


def ddl_primary_key(content: str) -> tuple[str, ...]:
    match = re.search(r"PRIMARY KEY\s*\((.*?)\)", content, re.DOTALL)
    return tuple(item.strip().rstrip(",") for item in match.group(1).splitlines() if item.strip())


class RawSpecsDdlTest(unittest.TestCase):
    def test_only_approved_raw_tables_are_registered(self):
        self.assertEqual(set(RAW_SPECS), set(RawTable))
        self.assertEqual(len(RAW_SPECS), 9)

    def test_repository_specs_match_ddl_columns_and_primary_keys(self):
        for spec in RAW_SPECS.values():
            content = (ROOT / "database" / "ddl" / spec.ddl_file).read_text(encoding="utf-8")
            columns = ddl_columns(content)
            self.assertEqual(spec.columns, tuple(column for column in columns if column != "created_at"), spec.ddl_file)
            self.assertNotIn("created_at", spec.columns)
            self.assertEqual(spec.primary_key, ddl_primary_key(content), spec.ddl_file)
