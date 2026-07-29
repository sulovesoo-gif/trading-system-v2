"""RAW 수집·저장 계층의 런타임 의존성 경계를 검증한다."""

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _requirement_names(path: Path) -> set[str]:
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#") or value.startswith("-"):
            continue
        names.add(value.split("==", 1)[0].lower())
    return names


class RuntimeDependencyTest(unittest.TestCase):
    def test_runtime_requirements_are_limited_to_raw_runtime_packages(self) -> None:
        requirements = _requirement_names(PROJECT_ROOT / "requirements.txt")

        self.assertEqual(
            requirements,
            {"requests", "python-dotenv", "psycopg[binary]", "psycopg_pool"},
        )
        self.assertTrue(
            {"numpy", "pandas", "scipy"}.isdisjoint(requirements)
        )

    def test_runtime_document_declares_python_310_support(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn(">=3.10, <3.15", readme)


if __name__ == "__main__":
    unittest.main()
