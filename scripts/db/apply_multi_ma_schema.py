"""테스트 DB에 공통코드·다중 MA 테이블을 비파괴적으로 적용한다."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.repository.database import DatabaseSettings, create_connection_pool


FILES = (
    "01_common_code_group.sql", "02_common_code.sql", "23_raw_stock_minute_snapshot.sql", "24_analysis_multi_ma.sql",
)
SEED = ROOT / "database" / "seed" / "02_common_code_initial.sql"


def main() -> int:
    if os.getenv("DB_INTEGRATION_TEST") != "1" or "test" not in os.getenv("DB_NAME", "").lower():
        raise RuntimeError("DB_INTEGRATION_TEST=1 및 test가 포함된 테스트 DB에서만 실행합니다.")
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        with pool.connection() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    for name in FILES:
                        cursor.execute((ROOT / "database" / "ddl" / name).read_text(encoding="utf-8"))
                    cursor.execute(SEED.read_text(encoding="utf-8"))
    finally:
        pool.close()
    print("공통코드·다중 MA 분석 스키마 적용 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
