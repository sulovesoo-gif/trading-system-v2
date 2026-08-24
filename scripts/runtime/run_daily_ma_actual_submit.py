"""Service entrypoint: deliberately fail-closed until a future explicit arm."""
from __future__ import annotations
import os
if os.getenv('DAILY_MA_ACTUAL_SEND','N') != 'Y':
 print('Daily MA actual submit runtime started: SEND_LOCKED')
else:
 raise SystemExit('Daily MA actual send requires a separately audited durable KIS transport binding')
