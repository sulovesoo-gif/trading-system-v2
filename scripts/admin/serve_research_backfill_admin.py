"""Local-only research administration page: http://127.0.0.1:8091/admin/backfill.

It is separate from dashboard and realtime collection. POST requests only invoke
explicit user-selected historical RAW backfill/replay; no order client exists.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.collector.raw.domestic_stock.holiday_calendar_collector import HolidayCalendarCollector
from src.collector.raw.domestic_stock.stock_daily_collector import StockDailyCollector
from src.collector.raw.domestic_stock.stock_historical_minute_collector import StockHistoricalMinuteCollector
from src.collector.raw.kis_client import KISClient
from src.repository.database import DatabaseSettings, create_connection_pool
from src.repository.raw_repository import RawRepository
from src.repository.research_repository import ResearchRepository
from src.service.kis_trading_calendar import KisTradingCalendar
from src.service.raw_ingestion_service import RawIngestionService
from src.service.research_backfill_service import CompleteResearchRunner, DailyCompleteResearchRunner, RegularAfterContinuousResearchRunner, ResearchBackfillService

PAGE = """<!doctype html><meta charset='utf-8'><title>Research Backfill</title>
<h1>연구용 RAW 백필 / COMPLETE 재생</h1><p>실시간 수집·알림·주문과 분리된 관리 기능입니다.</p>
<form method='post' action='/admin/backfill'><label>종목코드 <input name='stock_code' value='000660' required></label>
<label>종류 <select name='kind'><option value='minute'>분봉</option><option value='daily'>일봉</option></select></label>
<label>거래소 <select name='venue'><option value='INTEGRATED' selected>통합시장 (INTEGRATED)</option><option value='KRX'>KRX 정규장 (KRX)</option></select></label>
<label>시작일 <input type='date' name='start_date' required></label><label>종료일 <input type='date' name='end_date' required></label>
<label>진입 조건 <select name='entry_condition'><option value='MA10_CONFIRM' selected>MA10_CONFIRM</option><option value='SIGNAL_ONLY'>SIGNAL_ONLY</option></select></label><label>일봉 확인 MA 기간 <input name='ma_period' type='number' min='1' step='1' value='10'></label>
<button name='action' value='backfill'>RAW 백필 실행</button><button name='action' value='replay'>일별 COMPLETE 전략 재생 실행</button></form>"""

PAGE = PAGE.replace("</form>", "<button name='action' value='daily_replay'>Daily COMPLETE replay</button></form>")
PAGE = PAGE.replace("</form>", "<button name='action' value='continuous_replay'>Regular+After continuous replay</button></form>")


def _load_env(path: Path | None = None):
    path = path or ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1); os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

def application(pool, values):
    start = datetime.strptime(values['start_date'][0], '%Y-%m-%d').date(); end = datetime.strptime(values['end_date'][0], '%Y-%m-%d').date()
    client = KISClient(); raw = RawIngestionService(RawRepository(pool))
    service = ResearchBackfillService(minute_collector=StockHistoricalMinuteCollector(client), daily_collector=StockDailyCollector(client), raw_ingestion=raw,
        calendar=KisTradingCalendar(HolidayCalendarCollector(client)))
    code = values['stock_code'][0].strip(); kind = values['kind'][0]
    # Venue is explicit for research RAW backfill.  In particular, KRX daily
    # bars must remain separate from existing INTEGRATED daily rows so their
    # prior regular-session close can be used as an auditable reference.
    venue = values.get('venue', ['INTEGRATED'])[0].strip().upper()
    if venue not in {'INTEGRATED', 'KRX'}:
        raise ValueError('venue must be INTEGRATED or KRX')
    action = values.get('action', ['backfill'])[0]
    response = {}
    if action == 'backfill':
        result = service.backfill_minutes(stock_code=code,start_date=start,end_date=end,venue=venue) if kind == 'minute' else service.backfill_daily(stock_code=code,start_date=start,end_date=end,venue=venue)
        response['backfill'] = result.__dict__ if hasattr(result, '__dict__') else result
    elif action == 'replay':
        entry_condition = values.get('entry_condition', ['MA10_CONFIRM'])[0]
        response['research_run_id'] = str(CompleteResearchRunner(pool=pool, repository=ResearchRepository(pool)).run(start_date=start,end_date=end, entry_condition=entry_condition))
        response['raw_api_calls'] = 0
    elif action == 'daily_replay':
        entry_condition = values.get('entry_condition', ['MA10_CONFIRM'])[0]
        ma_period = int(values.get('ma_period', ['10'])[0])
        # Daily UI stores the generic policy name; legacy MA10 runs remain
        # readable as MA_CONFIRM period 10 in the daily dashboard.
        if entry_condition == 'MA10_CONFIRM': entry_condition = 'MA_CONFIRM'
        response['research_run_id'] = str(DailyCompleteResearchRunner(pool=pool, repository=ResearchRepository(pool)).run(start_date=start,end_date=end, entry_condition=entry_condition, confirm_period=ma_period))
        response['raw_api_calls'] = 0
    elif action == 'continuous_replay':
        entry_condition = values.get('entry_condition', ['MA10_CONFIRM'])[0]
        response['research_run_id'] = str(RegularAfterContinuousResearchRunner(pool=pool, repository=ResearchRepository(pool)).run(start_date=start,end_date=end, entry_condition=entry_condition))
        response['raw_api_calls'] = 0
    else: raise ValueError('unknown action')
    return response

class Handler(BaseHTTPRequestHandler):
    pool = None
    def _send(self, code, body, content_type='text/html; charset=utf-8'):
        self.send_response(code); self.send_header('Content-Type', content_type); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(body.encode())
    def do_GET(self):
        if urlparse(self.path).path != '/admin/backfill': return self._send(404, 'Not found')
        self._send(200, PAGE)
    def do_POST(self):
        if urlparse(self.path).path != '/admin/backfill': return self._send(404, 'Not found')
        length = int(self.headers.get('Content-Length','0')); values = parse_qs(self.rfile.read(length).decode())
        try: self._send(200, json.dumps(application(self.pool, values), default=str, ensure_ascii=False), 'application/json; charset=utf-8')
        except Exception as error: self._send(400, json.dumps({'error': f'{type(error).__name__}: {error}'}, ensure_ascii=False), 'application/json; charset=utf-8')
    def log_message(self, *_): pass

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--bind',default='127.0.0.1'); parser.add_argument('--port',type=int,default=8091); parser.add_argument('--env-file', type=Path); args=parser.parse_args(); _load_env(args.env_file)
    pool=create_connection_pool(DatabaseSettings.from_environment()); Handler.pool=pool
    try: ThreadingHTTPServer((args.bind,args.port), Handler).serve_forever()
    finally: pool.close()
if __name__ == '__main__': main()
