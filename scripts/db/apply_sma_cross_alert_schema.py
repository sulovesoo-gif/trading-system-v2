"""테스트 DB에 SMA 크로스 분석 테이블만 적용한다. RAW 테이블은 건드리지 않는다."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.repository.database import DatabaseSettings, create_connection_pool


ANALYSIS_DDL_FILES = (
    "18_analysis_sma_cross_signal.sql",
    "19_analysis_sma_cross_performance.sql",
    "20_analysis_signal_notification.sql",
    "21_analysis_sma_cross_related_bar.sql",
    "22_analysis_sma_cross_arm_state.sql",
)


def main() -> int:
    if os.getenv("DB_INTEGRATION_TEST") != "1" or "test" not in os.getenv("DB_NAME", "").lower():
        raise RuntimeError("DB_INTEGRATION_TEST=1 및 이름에 test가 포함된 테스트 DB에서만 실행합니다.")
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        with pool.connection() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    for name in ANALYSIS_DDL_FILES:
                        cursor.execute((ROOT / "database" / "ddl" / name).read_text(encoding="utf-8"))
    finally:
        pool.close()
    print("SMA 크로스 분석 테이블 적용 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
