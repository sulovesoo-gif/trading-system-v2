"""Bounded read-only detail checks. Optional FLOW_DETAIL_TEST_ENV uses real DB reads only."""
import os
import shutil
import subprocess
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from src.service.flow_v3_dashboard_service import _signal_window, strategy_detail_payload, trade_detail_payload, dashboard_payload

ROOT = Path(__file__).resolve().parents[1]


class DetailContractTest(unittest.TestCase):
    @unittest.skipUnless(os.getenv('FLOW_DETAIL_UI_URL') and os.getenv('PLAYWRIGHT_MODULE'), 'opt-in headless UI verification')
    def test_browser_desktop_mobile_clicks(self):
        script = r"""
const {chromium}=require(process.env.PLAYWRIGHT_MODULE),assert=require('assert');
(async()=>{const browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||undefined});try{
 for(const width of [1440,390]){const page=await browser.newPage({viewport:{width,height:844}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(process.env.FLOW_DETAIL_UI_URL);await page.locator('#rows tr.live').first().waitFor();
 await page.selectOption('#sort','per_trade');await page.waitForFunction(()=>!document.querySelector('#status').textContent.includes('조회 중'));
 await page.locator('tr.live[data-strategy="FV3008243"]').click();await page.locator('#tradeList button').first().waitFor();
 assert(await page.locator('#detail').evaluate(e=>e.open));assert.equal(await page.locator('#detailRange').inputValue(),'7');
 const box=await page.locator('#detail').boundingBox();assert(box.width<=width&&box.x>=0);
 await page.locator('#tradeList button').first().click();await page.locator('#signalDetail h3').first().waitFor();
 assert((await page.locator('#signalDetail').innerText()).includes('정상청산 신호'));
 await page.locator('#detailClose').click();assert(!(await page.locator('#detail').evaluate(e=>e.open)));
 assert.deepEqual(errors,[]);await page.close();
 }console.log('1440/390 strategy/trade clicks, modal bounds, JS errors: PASS');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
"""
        subprocess.run(['node','-e',script],check=True,timeout=90)

    def test_range_rejected_before_db(self):
        pool = MagicMock()
        with self.assertRaises(ValueError):
            strategy_detail_payload(pool, {'start':['2026-09-10'], 'end':['2026-09-01']})
        pool.connection.assert_not_called()

    def test_missing_exit_has_no_query(self):
        cur = MagicMock()
        self.assertEqual(_signal_window(cur, {}, None, 'exit'), [])
        cur.execute.assert_not_called()

    def test_pair_projection_and_bounds(self):
        cur = MagicMock()
        cur.description=[]
        cur.fetchall.return_value=[]
        _signal_window(cur, {'stock_code':'000660','exit_fast_period':10,'exit_slow_period':20},
                       datetime(2026,9,10,10,0), 'exit')
        sql,params = cur.execute.call_args.args
        self.assertIn('LIMIT 11',sql)
        self.assertEqual(params[:6],('10','20','10','20','08','08'))
        self.assertEqual((params[-1]-params[-2]).total_seconds(),600)

    @unittest.skipUnless(shutil.which('node'), 'node unavailable')
    def test_page_script_and_mobile_structure(self):
        script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(process.argv[1],'utf8');
new vm.Script(html.match(/<script>([\s\S]*?)<\/script>/)[1]);
for(const key of ['value="per_trade"','<dialog','94vw','overflow:auto','data-trade','data-strategy','showModal','signal-row','최근 7일','최근 30일'])assert(html.includes(key),key);
"""
        subprocess.run(['node','-e',script,str(ROOT/'reports/multi-ma/flow-v3.html')],check=True)


@unittest.skipUnless(os.getenv('FLOW_DETAIL_TEST_ENV'), 'opt-in DB read-only verification')
class DetailDatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from dotenv import load_dotenv
        from src.repository.database import DatabaseSettings,create_connection_pool
        load_dotenv(os.environ['FLOW_DETAIL_TEST_ENV'])
        cls.pool=create_connection_pool(DatabaseSettings.from_environment())

    @classmethod
    def tearDownClass(cls):
        cls.pool.close()

    def test_sorts_and_per_trade_ratio(self):
        for sort in ('strategy','capital','return','per_trade'):
            data=dashboard_payload(self.pool,{'sort':[sort]})
            self.assertEqual(data['total_count'],15)
            self.assertTrue(data['items'])
            if sort=='per_trade':
                ratios=[]
                for item in data['items']:
                    m=item['paper_cumulative'] or {}
                    ratios.append(float(m['net_pnl'])/int(m['closed_count']) if int(m.get('closed_count',0)) else float('-inf'))
                self.assertEqual(ratios,sorted(ratios,reverse=True))

    def test_default_week_and_trade_windows(self):
        data=strategy_detail_payload(self.pool,{'strategy_id':['FV3008243']})
        self.assertEqual((data['end']-data['start']).days,6)
        self.assertLessEqual(len(data['items']),50)
        self.assertTrue(data['items'],'recent strategy fixture has no trades')
        times=[(t['entry_signal_time'],t['paper_trade_id']) for t in data['items']]
        self.assertEqual(times,sorted(times,reverse=True))
        for row in data['items'][:1]:
            detail=trade_detail_payload(self.pool,{'strategy_id':['FV3008243'],'paper_trade_id':[str(row['paper_trade_id'])]})
            for kind,key in [('entry','entry_signal_time'),('exit','normal_exit_signal_time')]:
                rows=detail[kind+'_window'];signal=detail['trade'][key]
                self.assertLessEqual(len(rows),11)
                for state in rows:
                    self.assertLessEqual(abs((state['bar_time']-signal).total_seconds()),300)
                    self.assertEqual(state['is_signal'],state['bar_time']==signal)

    def test_closed_exit_and_wrong_strategy(self):
        with self.pool.connection() as conn,conn.transaction(),conn.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            cur.execute("SET LOCAL statement_timeout='5s'")
            cur.execute('''SELECT t.paper_trade_id,t.strategy_id FROM flow_v3_paper_trade t
                JOIN flow_v3_strategy_master m USING(strategy_id)
                WHERE t.normal_exit_signal_time IS NOT NULL
                AND EXISTS(SELECT 1 FROM flow_v3_minute_state s WHERE s.stock_code=m.stock_code AND s.bar_time=t.entry_signal_time)
                AND EXISTS(SELECT 1 FROM flow_v3_minute_state s WHERE s.stock_code=m.stock_code AND s.bar_time=t.normal_exit_signal_time)
                ORDER BY t.paper_trade_id DESC LIMIT 1''')
            trade_id,strategy_id=cur.fetchone()
        query={'strategy_id':[strategy_id],'paper_trade_id':[str(trade_id)]}
        data=trade_detail_payload(self.pool,query)
        self.assertTrue(data['entry_window'])
        self.assertTrue(data['exit_window'])
        self.assertEqual(data['exit_direction'],'하향교차' if data['strategy']['direction']=='LONG' else '상향교차')
        with self.assertRaises(KeyError):
            trade_detail_payload(self.pool,{**query,'strategy_id':['FV3005688' if strategy_id!='FV3005688' else 'FV3008243']})


if __name__=='__main__':
    unittest.main()
