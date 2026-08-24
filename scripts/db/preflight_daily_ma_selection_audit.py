"""Read-only hard guards for Daily MA selection snapshot input."""
from __future__ import annotations
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings

SQL = """
WITH metrics AS (
 SELECT p.strategy_id, count(*) AS n,
        (exp(sum(ln(1 + p.return_pct / 100.0))) - 1) * 100 AS compound_return,
        avg((p.return_pct > 0)::int) * 100 AS win_rate
   FROM daily_strategy_paper_trade p
   JOIN daily_strategy_master m USING(strategy_id)
  WHERE m.strategy_role='CANONICAL' AND m.is_enabled='Y'
    AND p.trade_status='CLOSED' AND p.return_pct IS NOT NULL
    AND p.entry_signal_date BETWEEN DATE '2026-05-27' AND DATE '2026-08-21'
    AND p.data_segment='POST_LISTING_ACTUAL'
    AND COALESCE(p.source_system,'') NOT LIKE '%TEST%'
  GROUP BY p.strategy_id
)
SELECT (SELECT count(*) FROM daily_strategy_master WHERE strategy_role='CANONICAL' AND is_enabled='Y') AS canonical,
       (SELECT count(*) FROM metrics WHERE compound_return > 0 AND win_rate >= 50) AS selected_346,
       (SELECT count(*) FROM daily_strategy_paper_trade
         WHERE source_system='DAILY_MA_V03_PRE0527_TREND_PROJECTION') AS pre0527_projection_rows,
       (SELECT count(DISTINCT strategy_id) FROM daily_strategy_paper_trade
         WHERE source_system='DAILY_MA_V03_PRE0527_TREND_PROJECTION') AS pre0527_projection_strategies;
"""
def main():
 load_dotenv(ROOT / '.env'); s=DatabaseSettings.from_environment()
 import psycopg
 with psycopg.connect(**s.connection_kwargs()) as c, c.cursor() as q:
  q.execute(SQL); row=q.fetchone()
 print(json.dumps({'canonical':row[0], 'selected_346':row[1], 'pre0527_projection_rows':row[2], 'pre0527_projection_strategies':row[3]}))
 if row[0] != 2400 or row[1] != 346 or row[2] != 5253 or row[3] != 1718:
  return 2
 return 0
if __name__ == '__main__': raise SystemExit(main())
