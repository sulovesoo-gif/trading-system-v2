"""Explicit TEMP-only validation launcher; never starts an operating runtime."""
import importlib
import os
from pathlib import Path
import sys
from urllib.parse import urlparse
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
PRODUCTION=Path('/home/ubuntu/projects/trading-system-v2')
if not str(ROOT).startswith('/tmp/codex-v181-approved-'):
    raise RuntimeError('Dedicated approved /tmp directory required')
sys.dont_write_bytecode=True
sys.path[:0]=[str(ROOT),str(PRODUCTION)]
for name in ('src','src.minute_ma','src.service','test'):
    package=importlib.import_module(name)
    package.__path__.insert(0,str(ROOT.joinpath(*name.split('.'))))

def forbidden(*args,**kwargs):
    raise AssertionError('External HTTP/KIS calls forbidden in TEMP validation')
urllib.request.urlopen=forbidden
import requests
original=requests.Session.request
def local_request(session,method,url,*args,**kwargs):
    parsed=urlparse(url)
    if parsed.hostname not in ('127.0.0.1','localhost') or not parsed.path.startswith('/sql-analysis/api/'):
        return forbidden()
    return original(session,method,url,*args,**kwargs)
requests.Session.request=local_request

os.environ['MINUTE_MA_TEST_ENV']=str(PRODUCTION/'.env')
os.environ['SQL_ANALYSIS_TEST_ENV']=str(PRODUCTION/'.env')
os.environ['TMPDIR']=str(ROOT)

if __name__=='__main__':
    import pytest
    raise SystemExit(pytest.main([
        '-q','-s','--tb=short','-p','no:cacheprovider',
        str(ROOT/'test/test_minute_ma_overnight_exit.py'),
        str(ROOT/'test/test_minute_ma_overnight_postgres.py'),
        str(ROOT/'test/test_sql_analysis_parallel.py'),
        str(ROOT/'test/test_sql_analysis_cancel.py'),
    ]+sys.argv[1:]))
