#!/usr/bin/env python3
from __future__ import annotations

import json,os,sys
from datetime import date,datetime
from decimal import Decimal
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlparse

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.service.minute_ma_real_dashboard_service import list_rows
from src.repository.database import DatabaseSettings,create_connection_pool

load_dotenv(ROOT/".env")
POOL=create_connection_pool(DatabaseSettings.from_environment())
PAGE=ROOT/"reports"/"multi-ma"/"minute-ma-real.html"

def _json(value):
    if isinstance(value,(date,datetime)): return value.isoformat()
    if isinstance(value,Decimal): return float(value)
    raise TypeError(type(value).__name__)

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed=urlparse(self.path)
        if parsed.path in ("/","/minute-ma-real.html"):
            payload=PAGE.read_bytes(); self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",str(len(payload))); self.end_headers(); self.wfile.write(payload); return
        if parsed.path=="/api/minute-ma-real":
            q=parse_qs(parsed.query)
            raw_date=q.get("date",[None])[0]
            result=list_rows(POOL,snapshot_date=date.fromisoformat(raw_date) if raw_date else None,
                filter_code=q.get("filter",[None])[0],sort=q.get("sort",["cumulative_rank"])[0],
                limit=int(q.get("limit",[100])[0]),offset=int(q.get("offset",[0])[0]))
            payload=json.dumps(result,default=_json,ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Content-Length",str(len(payload))); self.end_headers(); self.wfile.write(payload); return
        self.send_error(404)

if __name__=="__main__":
    ThreadingHTTPServer(("127.0.0.1",int(os.getenv("MINUTE_MA_REAL_DASHBOARD_PORT","8096"))),Handler).serve_forever()
