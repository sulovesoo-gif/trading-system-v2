#!/usr/bin/env python3
"""Loopback-only, read-only dashboard using the existing stdlib HTTP pattern."""
import argparse
import json
import sys
from datetime import date,datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from src.service.first_rise_status_service import snapshot

PAGE=ROOT/'reports/first-rise/status.html'


def serialize(value):
    if isinstance(value,(date,datetime)):return value.isoformat()
    if isinstance(value,Decimal):return str(value)
    raise TypeError(type(value).__name__)


def handler(load):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path=urlparse(self.path).path
            if path=='/':
                body=PAGE.read_bytes();kind='text/html; charset=utf-8';status=200
            elif path in ('/api/status','/first-rise/api/status'):
                try:
                    body=json.dumps(load(),ensure_ascii=False,default=serialize).encode();status=200
                except Exception:
                    body=b'{"error":"STATUS_READ_UNAVAILABLE"}';status=503
                kind='application/json; charset=utf-8'
            else:self.send_error(404);return
            self.send_response(status)
            self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers();self.wfile.write(body)

        def do_POST(self):self.send_error(405,'READ ONLY')
        do_PUT=do_DELETE=do_PATCH=do_POST
    return Handler


def main():
    from dotenv import load_dotenv
    from src.repository.database import DatabaseSettings,create_connection_pool
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8098)
    args=parser.parse_args();load_dotenv(ROOT/'.env')
    pool=create_connection_pool(DatabaseSettings.from_environment())
    server=ThreadingHTTPServer(('127.0.0.1',args.port),handler(lambda:snapshot(pool,datetime.now(ZoneInfo('Asia/Seoul')).date())))
    try:server.serve_forever()
    finally:server.server_close();pool.close()


if __name__=='__main__':main()
