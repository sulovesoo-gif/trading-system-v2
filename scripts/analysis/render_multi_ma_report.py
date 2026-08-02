"""DB 요약을 외부 CDN 없는 정적 HTML 리포트로 생성한다."""

from __future__ import annotations

import html
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.repository.database import DatabaseSettings, create_connection_pool


def main() -> int:
    output = ROOT / "reports" / "multi_ma_ranking.html"
    output.parent.mkdir(exist_ok=True)
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        with pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT stock_code, trading_venue, strategy_code, analysis_slot, cumulative_pnl, cumulative_return, "
                    "trade_count, win_count, maximum_drawdown FROM analysis_multi_ma_summary ORDER BY cumulative_return DESC"
                )
                rows = cursor.fetchall()
    finally:
        pool.close()
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in (index, *row)) + "</tr>"
        for index, row in enumerate(rows, 1)
    )
    output.write_text(
        "<!doctype html><meta charset='utf-8'><title>Trading System V2 다중 MA 성과</title>"
        "<style>body{font-family:sans-serif;margin:2rem}table{border-collapse:collapse;width:100%}td,th{padding:.5rem;border:1px solid #ddd}</style>"
        "<h1>다중 MA 전략 성과 순위</h1><table><thead><tr><th>순위</th><th>종목</th><th>마켓</th><th>전략</th><th>기준</th><th>누적손익</th><th>누적수익률</th><th>거래수</th><th>승수</th><th>최대낙폭</th></tr></thead>"
        f"<tbody>{body}</tbody></table>", encoding="utf-8"
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
