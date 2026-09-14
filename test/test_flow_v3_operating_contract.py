"""Local fixtures only; no production .env, network, broker or service operations."""
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch,MagicMock,AsyncMock
import asyncio
import json
import subprocess
import shutil
from src.flow_v3.live_scheduler import priority_exits,wait_seconds,eod_priority
from src.flow_raw.collector import liveness_reconnect_reason,MARKET_LIVENESS_GRACE_SECONDS
from src.flow_v3.live_operations import LiveOperations
from src.service.flow_v3_dashboard_service import dashboard_payload,_live_operations
from test.test_flow_v3_send_authorization import Connection
from test import test_flow_v3_send_authorization as send_tests

ROOT=Path(__file__).resolve().parents[1]


class OperatingContractTests(TestCase):
    def test_capital_invalid_before_db(self):
        pool=MagicMock()
        for value in ('-1','NaN','Infinity','1e18'):
            with self.assertRaises(ValueError):
                LiveOperations(pool).set_capital('A','UNDERLYING',value,'REF',datetime(2026,9,14))
        pool.connection.assert_not_called()

    def test_priority_same_cycle_release_cancel_history_quote_sell(self):
        calls=[]
        repo=SimpleNamespace(cycle=lambda *a,**k:calls.append(('cycle',k)),quote_codes=lambda:['000660'])
        reader=SimpleNamespace(poll=lambda *a,**k:(calls.append(('poll',k)) or 1),
                               quotes=lambda *a:(calls.append(('quotes',{})) or {}))
        transport=SimpleNamespace(run=lambda **k:(calls.append(('post',k)) or 1))
        result=priority_exits(repo,reader,transport,lambda:datetime(2026,9,14,15,19,1))
        self.assertEqual([c[0] for c in calls],['cycle','poll','quotes','cycle','post','poll','quotes','cycle','post'])
        self.assertEqual(result,dict(priority_posts=2,priority_polled=2))
        self.assertTrue(all(k.get('exits_only') for name,k in calls if name!='quotes'))
        calls.clear()
        priority_exits(repo,reader,transport,lambda:datetime(2026,9,14,15,20))
        self.assertEqual(calls,[])

    def test_wake_at_eod_boundary(self):
        self.assertEqual(wait_seconds(datetime(2026,9,14,15,17,59,500000)),.5)
        for minute in (18,19):
            self.assertTrue(eod_priority(datetime(2026,9,14,15,minute)))
            self.assertEqual(wait_seconds(datetime(2026,9,14,15,minute)),1)
        self.assertFalse(eod_priority(datetime(2026,9,14,15,20)))

    def test_afterhours_liveness_grace_not_strategy_window(self):
        self.assertEqual(MARKET_LIVENESS_GRACE_SECONDS,60)
        for hour,minute,second in ((15,31,0),(16,0,0),(19,59,59),(20,0,59)):
            now=datetime(2026,9,14,hour,minute,second)
            self.assertEqual(liveness_reconnect_reason(connected_at=now-timedelta(hours=1),
                last_data_frame_at=now-timedelta(seconds=60),now=now),'MARKET_DATA_SILENCE_60S')
        now=datetime(2026,9,14,20,1)
        self.assertIsNone(liveness_reconnect_reason(connected_at=now-timedelta(hours=1),last_data_frame_at=now-timedelta(minutes=1),now=now))
        source=(ROOT/'src/flow_v3/repository.py').read_text()
        self.assertIn('15:31',source)
        self.assertNotIn('20:00',source)

    def test_afterhours_raw_frames_and_duplicates_are_saved_losslessly(self):
        from src.flow_raw.collector import FlowRawCollector
        from test.test_flow_raw_collector import frame
        from src.flow_raw.contracts import TR_EXECUTION,EXECUTION_FIELDS
        for hour in (16,19,20):
            now=datetime(2026,9,14,hour,0)
            raw=frame(TR_EXECUTION,EXECUTION_FIELDS,[{'MKSC_SHRN_ISCD':'000660',
                'STCK_CNTG_HOUR':f'{hour:02}0000','BSOP_DATE':'20260914'}])
            ack=json.dumps({'header':{},'body':{'rt_cd':'0'}})
            socket=SimpleNamespace(send=AsyncMock(),recv=AsyncMock(side_effect=[ack]*12+[raw,raw,asyncio.CancelledError()]))
            context=MagicMock();context.__aenter__=AsyncMock(return_value=socket);context.__aexit__=AsyncMock(return_value=False)
            repository=MagicMock();repository.recent_hashes.return_value=[]
            integrated=MagicMock();integrated.recent_hashes.return_value=[]
            collector=FlowRawCollector(repository,integrated_repository=integrated,ws_url='fixture',
                approval_provider=lambda:'fake',now_provider=lambda:now)
            with patch.dict('sys.modules',{'websockets':SimpleNamespace(connect=lambda *a,**k:context)}),patch('src.flow_raw.collector.asyncio.sleep',new_callable=AsyncMock):
                with self.assertRaises(asyncio.CancelledError): asyncio.run(collector.run_forever())
            calls=repository.save_event.call_args_list
            self.assertEqual(len(calls),2)
            self.assertEqual([x.kwargs['duplicate_flag'] for x in calls],[False,True])
            self.assertEqual([x.kwargs['receive_sequence'] for x in calls],[1,2])
            self.assertEqual(calls[0].args[0].payload_hash,calls[1].args[0].payload_hash)
            self.assertEqual(socket.send.call_count,12)
            integrated.save_event.assert_not_called()

    def test_post_clock_checked_after_cash_inquiry(self):
        class Expired(Connection):
            def execute(self,sql,args=None):
                result=super().execute(sql,args)
                if 'SELECT (i.signal_time::date=' in sql: self.row=(False,)
                return result
        conn=Expired()
        first,calls,_=send_tests.SendTests().run_fake(conn)
        self.assertEqual((first,calls),(0,[]))
        self.assertTrue(any(args and 'EXECUTION_WINDOW_CLOSED_OR_REFERENCE_STALE' in args for _,args in conn.statements))

    def test_dashboard_current_routes_default_tiebreakers(self):
        pool=MagicMock();cur=pool.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect=[(0,),(None,),(None,),(9600,9600),(0,0)]
        cur.fetchall.return_value=[]
        result=dashboard_payload(pool,{})
        sql='\n'.join(call.args[0] for call in cur.execute.call_args_list)
        self.assertNotIn('FV300',sql)
        self.assertIn('op.effective_to IS NULL',sql)
        self.assertIn("compound_return_pct')::numeric DESC NULLS LAST,((c.metrics->>'net_pnl')",sql)
        self.assertEqual(result['items'],[])
        for sort in ('profit','trades','mdd','per_trade','capital','strategy'):
            cur.fetchone.side_effect=[(0,),(None,),(None,),(9600,9600),(0,0)]
            self.assertEqual(dashboard_payload(pool,{'sort':[sort]})['total_count'],0)
        _live_operations(cur,'FIXTURE')
        sql=cur.execute.call_args.args[0]
        self.assertIn("WHEN 'UNDERLYING' THEN 0",sql)
        self.assertIn('NULLIF(s.closed_count,0)',sql)
        self.assertIn('WHERE operation_id=o.operation_id',sql)

    @__import__('unittest').skipUnless(shutil.which('node'),'Node required for HTML render test')
    def test_dashboard_render_one_paper_multiple_live_and_zero_na(self):
        script=r"""
const fs=require('fs'),assert=require('assert');
const html=fs.readFileSync(process.argv[1],'utf8'),code=html.split('<script>')[1].split('</script>')[0];
new Function(code); // Compile the complete existing modal/snapshot script too.
const render=new Function(code.slice(code.indexOf('const esc='),code.indexOf('async function load()'))+';return render;')();
const item={strategy_id:'TEST',stock_code:'000660',execution_code:'0193T0',direction:'LONG',paper_cumulative:{closed_count:0},paper_period:{},live_operations:[]};
let s=render(item);assert.equal((s.match(/class="paper"/g)||[]).length,1);assert(!s.includes('class="live"'));assert(s.includes('선택 기간 실제 거래 0건'));
item.live_operations=['UNDERLYING','LEVERAGE','INVERSE'].map((r,i)=>({operation_id:i+1,execution_route:r,live_execution_code:i?'0193T0':'000660',allocated_amount:0,closed_count:0,profit_per_trade:null}));
s=render(item);assert.equal((s.match(/class="paper"/g)||[]).length,1);assert.equal((s.match(/class="live"/g)||[]).length,3);assert(s.includes('rowspan="4"'));
assert(s.indexOf('LIVE · 본주')<s.indexOf('LIVE · 레버리지'));assert(s.includes('거래당 해당없음'));assert(s.includes('기존 보유 EXIT 유지'));assert(!s.includes('NO SEND'));
assert(html.includes('자본효율 연구/참고'));assert(html.includes('data-strategy'));assert(!html.includes('승인 후보 15'));
console.log('Dashboard full JS compile / 1 PAPER + 3 LIVE / zero N/A / modal identity: PASS');
"""
        subprocess.run(['node','-e',script,str(ROOT/'reports/multi-ma/flow-v3.html')],check=True,timeout=15)
