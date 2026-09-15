"""Standalone local-first Leadership HTTP service, GET only, no trading imports."""
import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import psycopg
from src.service.flow_v3_leadership_dashboard_service import options,ranking,history


def handler(dsn):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url=urlparse(self.path)
            if url.path in ('/','/flow-v3-leadership.html'):
                content=(ROOT/'reports/multi-ma/flow-v3-leadership.html').read_bytes()
                self.respond(200,content,'text/html; charset=utf-8');return
            action={'/leadership/api/options':options,'/leadership/api/ranking':ranking,
                    '/leadership/api/history':history}.get(url.path)
            if not action: self.respond(404,b'Not found','text/plain');return
            try:
                with psycopg.connect(dsn) as conn:
                    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                    conn.execute("SET LOCAL statement_timeout='8s'")
                    result=action(conn) if action is options else action(conn,parse_qs(url.query))
                self.respond(200,json.dumps(result,default=str,ensure_ascii=False).encode(),'application/json; charset=utf-8')
            except (ValueError,TypeError):
                self.respond(400,b'{"error":"INVALID_PARAMETERS_OR_CONFIG"}','application/json')
            except psycopg.Error:
                self.respond(503,b'{"error":"LEADERSHIP_DATA_UNAVAILABLE"}','application/json')

        def respond(self,code,body,kind):
            self.send_response(code);self.send_header('Content-Type',kind)
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    return Handler


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--bind',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8094);args=parser.parse_args()
    dsn=os.environ.get('LEADERSHIP_READ_DSN')
    if not dsn: parser.error('LEADERSHIP_READ_DSN is required; no trading credential fallback')
    server=ThreadingHTTPServer((args.bind,args.port),handler(dsn))
    print(f'Leadership http://{args.bind}:{args.port}/flow-v3-leadership.html',flush=True)
    try: server.serve_forever()
    finally: server.server_close()


if __name__=='__main__': main()
