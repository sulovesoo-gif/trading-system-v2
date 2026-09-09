"""Operator-only FLOW approval. Default --check is read-only; never sends a test order.

--enable starts REAL automatic trading on subsequent natural FLOW signals.
The assistant deployment must run only --check and leave both gates N.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.flow_v3.live_contract import WHITELIST, validate_mapping

UNIT = 'trading-flow-v3-live-nosend.service'  # Retained name: no second worker.
DROPIN = Path('/etc/systemd/system') / (UNIT + '.d') / 'actual-send.conf'


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout.strip()


def install_gate(value):
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf') as f:
        f.write('[Service]\nEnvironment=FLOW_V3_ACTUAL_SEND=' + value + '\n')
        f.flush()
        run('sudo','-n','install','-d','-m','0755',str(DROPIN.parent))
        run('sudo','-n','install','-m','0644',f.name,str(DROPIN))
    run('sudo','-n','systemctl','daemon-reload')


def running_gate():
    pid=run('systemctl','show',UNIT,'--property=MainPID','--value')
    if not pid.isdigit() or int(pid)<=0:
        raise ValueError('FLOW_WORKER_PID_MISSING')
    entries=Path('/proc',pid,'environ').read_bytes().split(b'\0')
    values=[e.split(b'=',1)[1].decode() for e in entries if e.startswith(b'FLOW_V3_ACTUAL_SEND=')]
    return values[0] if len(values)==1 else 'N'


def preflight(c):
    rows=c.execute("""SELECT c.strategy_id,m.stock_code,m.direction,m.execution_code,
        o.operation_status,o.effective_to,c.initial_capital,c.current_capital,c.realized_net
        FROM flow_v3_live_capital c JOIN flow_v3_strategy_master m USING(strategy_id)
        JOIN flow_v3_strategy_operation o ON o.operation_id=c.operation_id ORDER BY c.strategy_id""").fetchall()
    if {r[0] for r in rows} != set(WHITELIST) or len(rows)!=15:
        raise ValueError('FLOW_APPROVED_15_MISMATCH')
    for sid,stock,direction,code,status,ended,initial,current,net in rows:
        validate_mapping(sid,stock,direction,code)
        if status!='LIVE' or ended is not None or current!=initial+net:
            raise ValueError('FLOW_OPERATION_OR_CAPITAL_INVALID')
    row=c.execute("SELECT enabled FROM flow_v3_send_profile WHERE profile_code='FLOW_V3_LIVE_SEND'").fetchone()
    if row is None or row[0] not in ('Y','N'):
        raise ValueError('FLOW_APPROVAL_PROFILE_MISSING')
    return row[0]


def main():
    from dotenv import load_dotenv
    import psycopg
    from src.repository.database import DatabaseSettings
    p=argparse.ArgumentParser(description=__doc__)
    choice=p.add_mutually_exclusive_group()
    choice.add_argument('--check',action='store_true')
    choice.add_argument('--enable',action='store_true',help='OPERATOR ONLY: enable real natural-signal trading')
    args=p.parse_args()
    load_dotenv(ROOT/'.env')
    with psycopg.connect(**DatabaseSettings.from_environment().connection_kwargs(),autocommit=True) as c:
        with c.transaction():
            c.execute('SET TRANSACTION READ ONLY')
            db=preflight(c)
        active=run('systemctl','is-active',UNIT)
        if not args.enable:
            print(json.dumps(dict(mode='READ_ONLY_CHECK',db_gate=db,env_gate=running_gate(),service=active,whitelist=15,
                activation_performed=False)))
            return
        # Explicit human action only. No PAPER/Minute/Daily settings or ledgers.
        try:
            install_gate('Y')  # Running process retains N until the restart below.
            with c.transaction():
                preflight(c)
                c.execute("""UPDATE flow_v3_send_profile SET enabled='Y',updated_at=clock_timestamp(),
                    updated_by='HUMAN_FLOW_V3_SEND_APPROVAL' WHERE profile_code='FLOW_V3_LIVE_SEND' AND enabled<>'Y'""")
            run('sudo','-n','systemctl','restart',UNIT)
            run('systemctl','is-active',UNIT)
            if running_gate()!='Y' or preflight(c)!='Y':
                raise ValueError('FLOW_ACTUAL_APPROVAL_MISMATCH')
        except Exception:
            # Known configuration failure: keep FLOW off; never touch other families.
            c.execute("""UPDATE flow_v3_send_profile SET enabled='N',updated_at=clock_timestamp(),
                updated_by='FLOW_ACTIVATION_FAILURE_OFF' WHERE profile_code='FLOW_V3_LIVE_SEND'""")
            install_gate('N')
            run('sudo','-n','systemctl','restart',UNIT)
            raise
        print(json.dumps(dict(service='active',db_gate='Y',configured_env_gate='Y',
            whitelist=15,test_orders_sent=0,natural_signal_trading_enabled=True)))


if __name__=='__main__':
    main()
