"""Explicit non-destructive classification of frozen Champion registry rows."""
from __future__ import annotations
import os
from src.repository.database import DatabaseSettings, create_connection_pool

CANONICAL_NAMES = ('LIVE_HYNIX_S3_3BAR','LIVE_HYNIX_S3_5BAR','LIVE_SAMSUNG_S1_LONG','LIVE_SAMSUNG_S2_SHORT')

def main() -> int:
    if os.getenv('CLASSIFY_LIVE_STRATEGY_ROLES') != 'YES': raise SystemExit('set CLASSIFY_LIVE_STRATEGY_ROLES=YES')
    pool=create_connection_pool(DatabaseSettings.from_environment())
    try:
      with pool.connection() as c, c.cursor() as q:
        q.execute("UPDATE research_live_strategy SET instance_role='LEGACY_SMOKE_TRANSITIONAL' WHERE live_name='7C_SAMSUNG_S1_LONG_SMOKE'")
        q.execute("UPDATE research_live_strategy SET instance_role='CANONICAL_LIVE' WHERE live_name = ANY(%s)",(list(CANONICAL_NAMES),))
        q.execute("SELECT count(*) FROM research_live_strategy WHERE instance_role='CANONICAL_LIVE'")
        if q.fetchone()[0] != 4: raise RuntimeError('canonical registry must contain exactly four rows')
        c.commit()
    finally: pool.close()
    print('CLASSIFIED canonical=4 legacy_smoke_preserved=Y'); return 0
if __name__=='__main__': raise SystemExit(main())
