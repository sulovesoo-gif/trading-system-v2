import os
import unittest
from unittest.mock import patch

from scripts.runtime.apply_execution_additive_ddls import DDL_FILES, main


class ExecutionMigrationRunnerTest(unittest.TestCase):
    def test_requires_explicit_environment_gate_before_database_import(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit):
            main()

    def test_only_declares_the_two_additive_execution_files(self):
        self.assertEqual(DDL_FILES, ("database/ddl/36_execution_ownership.sql", "database/ddl/37_forward_observation.sql", "database/ddl/38_live_strategy_instance_role.sql"))
