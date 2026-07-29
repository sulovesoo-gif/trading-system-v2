from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPTS = tuple((ROOT / "scripts" / "backfill" / name) for name in (
    "_futures_minute_backfill_common.sh",
    "run_full_futures_minute_backfill.sh",
    "resume_futures_minute_backfill.sh",
    "verify_futures_minute_backfill.sh",
))


def find_bash() -> str | None:
    for path in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe", shutil.which("bash")):
        if path and Path(path).is_file():
            return path
    return None


class FuturesBackfillShellScriptTest(unittest.TestCase):
    def _run_bash(self, command: str) -> subprocess.CompletedProcess[str]:
        bash = find_bash()
        if bash is None:
            self.skipTest("Bash 실행 파일이 없습니다.")
        return subprocess.run(
            [bash, "-c", f'export PATH="/usr/bin:/bin:$PATH"; {command}'],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_shell_syntax(self):
        bash = find_bash()
        if bash is None:
            self.skipTest("Bash 실행 파일이 없습니다.")
        for script in SCRIPTS:
            result = subprocess.run([bash, "-n", script.relative_to(ROOT).as_posix()], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_scripts_guard_test_database_and_numeric_job_id(self):
        common = SCRIPTS[0].read_text(encoding="utf-8")
        self.assertIn('DB_NAME:-}" == "trading_system_v2_test', common)
        self.assertIn('[[ "$1" =~ ^[0-9]+$ ]]', common)
        self.assertIn("FUTURES_MINUTE_KRX", common)
        self.assertIn("raw_futures_minute", common)
        self.assertIn("futs_prpr", common)
        self.assertIn("export DB_INTEGRATION_TEST=1", common)
        self.assertIn("선물 백필 검증 판정: PASS", common)
        self.assertNotIn(":'job_id'", common)
        self.assertNotIn("-v job_id=", common)

    def test_full_script_has_dry_run_without_worker_start(self):
        content = SCRIPTS[1].read_text(encoding="utf-8")
        self.assertIn("--dry-run", content)
        self.assertIn("run_futures_minute_backfill.py", common := SCRIPTS[0].read_text(encoding="utf-8"))
        self.assertLess(content.index('if [[ "${1:-}" == "--dry-run" ]]'), content.index("futures_bootstrap"))
        self.assertIn("futures_print_status", content)
        result = self._run_bash("scripts/backfill/run_full_futures_minute_backfill.sh --dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dry-run", result.stdout)

    def test_resume_and_verify_validate_argument(self):
        for script in SCRIPTS[2:]:
            content = script.read_text(encoding="utf-8")
            self.assertIn("futures_assert_job_id", content)


if __name__ == "__main__":
    unittest.main()
