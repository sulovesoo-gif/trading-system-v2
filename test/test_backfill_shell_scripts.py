from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPTS = (
    ROOT / "scripts" / "backfill" / "_stock_minute_backfill_common.sh",
    ROOT / "scripts" / "backfill" / "run_full_stock_minute_backfill.sh",
    ROOT / "scripts" / "backfill" / "resume_stock_minute_backfill.sh",
    ROOT / "scripts" / "backfill" / "verify_stock_minute_backfill.sh",
)


def find_bash() -> str | None:
    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        os.getenv("BASH_BIN"),
        shutil.which("bash"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


class BackfillShellScriptTest(unittest.TestCase):
    def _run_git_bash(self, command: str) -> subprocess.CompletedProcess[str]:
        bash = find_bash()
        if bash is None:
            self.skipTest("Bash 실행 파일이 없어 셸 실행 검사를 건너뜁니다.")
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
            self.skipTest("Bash 실행 파일이 없어 셸 문법 검사를 건너뜁니다.")
        for script in SCRIPTS:
            result = subprocess.run(
                [bash, "-n", script.relative_to(ROOT).as_posix()],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=ROOT,
            )
            self.assertEqual(result.returncode, 0, f"{script.name}: {result.stderr}")

    def test_full_script_has_dry_run_and_test_db_guard(self):
        content = (ROOT / "scripts" / "backfill" / "run_full_stock_minute_backfill.sh").read_text(encoding="utf-8")
        self.assertIn("--dry-run", content)
        self.assertIn("backfill_bootstrap", content)
        common = SCRIPTS[0].read_text(encoding="utf-8")
        self.assertIn('DB_NAME" == "trading_system_v2_test"', common)
        self.assertIn("backfill_require_container", common)
        self.assertIn("backfill_verify_job", common)

    def test_resume_rejects_missing_invalid_and_smoke_job_id(self):
        content = (ROOT / "scripts" / "backfill" / "resume_stock_minute_backfill.sh").read_text(encoding="utf-8")
        self.assertIn('[[ $# -ge 1 && $# -le 2 ]]', content)
        self.assertIn('[[ "$requested_job_id" =~ ^[0-9]+$ ]]', content)
        self.assertIn('[[ "$requested_job_id" != "1" ]]', content)
        self.assertIn("backfill_read_resume_job", content)

        invalid = self._run_git_bash('"$BASH" scripts/backfill/resume_stock_minute_backfill.sh invalid')
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("job_id", invalid.stdout + invalid.stderr)

        smoke = self._run_git_bash('"$BASH" scripts/backfill/resume_stock_minute_backfill.sh 1')
        self.assertEqual(smoke.returncode, 2)
        self.assertIn("job_id=1", smoke.stdout + smoke.stderr)

    def test_job_id_wait_and_verification_sql_failure_handling_exist(self):
        content = SCRIPTS[0].read_text(encoding="utf-8")
        self.assertIn("backfill_wait_for_new_job", content)
        self.assertIn("WHERE job_id > ${baseline_job_id}", content)
        self.assertIn('[[ "$job_id" =~ ^[0-9]+$ ]]', content)
        self.assertIn("WHERE job_id = ${job_id}", content)
        self.assertNotIn(":'job_id'", content)
        self.assertNotIn("-v job_id=", content)
        self.assertIn("완료 검증 SQL 실행에 실패", content)
        self.assertIn("raw_payload ? 'stck_bsop_date'", content)

    def test_resume_sql_uses_validated_numeric_job_id_without_psql_variable(self):
        content = (ROOT / "scripts" / "backfill" / "resume_stock_minute_backfill.sh").read_text(encoding="utf-8")
        self.assertIn('[[ "$requested_job_id" =~ ^[0-9]+$ ]]', content)
        self.assertIn("backfill_assert_job_id", content)
        self.assertIn("WHERE job_id = ${BACKFILL_JOB_ID}", content)
        self.assertNotIn(":'job_id'", content)
        self.assertNotIn("-v job_id=", content)

    def test_final_verification_script_uses_database_only_bootstrap_and_validated_job_id(self):
        content = (ROOT / "scripts" / "backfill" / "verify_stock_minute_backfill.sh").read_text(encoding="utf-8")
        self.assertIn("backfill_bootstrap_database_only", content)
        self.assertIn("backfill_assert_job_id", content)
        self.assertIn("backfill_print_status", content)
        self.assertIn("backfill_verify_job", content)
        self.assertIn("--dry-run", content)

        invalid = self._run_git_bash('"$BASH" scripts/backfill/verify_stock_minute_backfill.sh invalid')
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("job_id", invalid.stdout + invalid.stderr)

    def test_dry_run_requires_no_test_artifact(self):
        content = (ROOT / "scripts" / "backfill" / "run_full_stock_minute_backfill.sh").read_text(encoding="utf-8")
        dry_run_start = content.index("if (( dry_run )); then")
        calendar_start = content.index("backfill_calculate_plan")
        dry_run_block = content[dry_run_start:calendar_start]
        self.assertIn("backfill_print_dry_run_plan", dry_run_block)
        self.assertNotIn("backfill_run_worker", dry_run_block)

    def test_dry_run_rejects_missing_environment_and_unhealthy_container(self):
        content = SCRIPTS[0].read_text(encoding="utf-8")
        self.assertIn("필수 환경변수가 없습니다", content)
        self.assertIn("DB_NAME은 trading_system_v2_test여야 합니다", content)
        self.assertIn("TimescaleDB 컨테이너가 healthy 상태가 아닙니다", content)


if __name__ == "__main__":
    unittest.main()
