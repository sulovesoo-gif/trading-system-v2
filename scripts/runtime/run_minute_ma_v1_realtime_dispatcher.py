"""Trigger existing V1 runtimes from durable realtime-bar finalization."""
from __future__ import annotations
import logging,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.minute_ma.realtime_dispatch import MinuteV1RealtimeDispatcher,MinuteV1RealtimeDispatchRepository
from src.repository.database import DatabaseSettings,create_connection_pool

def main()->int:
    load_dotenv(ROOT/'.env');logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    pool=create_connection_pool(DatabaseSettings.from_environment())
    commands={
      'V1_PAPER':(sys.executable,str(ROOT/'scripts/runtime/run_minute_ma_v1_paper.py'),'--date-current','--write'),
      'V1_LIVE':(sys.executable,str(ROOT/'scripts/runtime/run_minute_ma_actual.py')),
    }
    dispatcher=MinuteV1RealtimeDispatcher(MinuteV1RealtimeDispatchRepository(pool),commands=commands,
      runner=lambda command:subprocess.run(command,cwd=ROOT,check=False).returncode)
    try:
      while True:
        result=dispatcher.poll_once()
        if result:logging.info('Minute V1 realtime dispatch=%s',result)
        time.sleep(.1)
    finally:pool.close()

if __name__=='__main__':raise SystemExit(main())
