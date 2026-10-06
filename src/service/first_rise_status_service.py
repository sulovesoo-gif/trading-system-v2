"""Read-only FIRST_RISE operations view. No broker/runtime imports or actions."""
import json
import re
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

UNITS=('trading-flow-raw-collector.service','trading-first-rise-live.service')
KST=ZoneInfo('Asia/Seoul')


def redact(value):
    if isinstance(value,dict):
        return {k:('[REDACTED]' if re.search(r'token|secret|password|appkey|authorization|cano|account_number',k,re.I)
                   else redact(v)) for k,v in value.items()}
    if isinstance(value,list):return [redact(v) for v in value]
    if isinstance(value,str):
        value=re.sub(r'(?i)(postgres(?:ql)?://)[^@\s]+@',r'\1[REDACTED]@',value)
        value=re.sub(r'(?i)(Bearer\s+)\S+',r'\1[REDACTED]',value)
        return re.sub(r'''(?i)((?:access_token|appkey|appsecret|password|authorization|cano)["']?\s*[:=]\s*)["']?[^\s,}"']+''',r'\1[REDACTED]',value)
    return value


def log_text(row):
    return f"[{row['time']} KST]\nservice={row['service']}\nlevel={row['level']}\nmessage={row['message']}"


def signal_text(row):
    return (f"{row['time']} | {row['stock']} {row['name'] or ''} | {row['sequence']} | {row['status']}\n"
        f"신호 {row['signal_price']} → BUY {row['buy_quantity']}주 @{row['buy_average']}"
        f" → SELL {row['sell_quantity']}주 @{row['sell_average']}\n"
        f"독립 시장청산={row['market_exit_reason']} 신호 {row['market_exit_signal_time']} 실행 {row['market_exit_time']}\n"
        f"5초 보호청산=조건 {row['protection_trigger_time']} 기준 {row['protection_reference_price']} 관찰 {row['protection_observed_price']}\n"
        f"실제 청산=주문 {row['actual_sell_order_time']} 체결관찰 {row['actual_exit_time']}\n"
        f"{row['pnl_label']}={row['net_pnl']}원\n"+json.dumps(redact(row['evidence']),ensure_ascii=False,default=str))


def operation_logs(run=subprocess.run):
    services=[];logs=[];last_refresh=None;access='OK'
    for unit in UNITS:
        service={'name':unit,'ActiveState':'UNKNOWN','MainPID':None,'ExecMainStartTimestamp':None}
        try:
            out=run(['systemctl','show',unit,'-p','ActiveState','-p','MainPID','-p','ExecMainStartTimestamp'],
                    capture_output=True,text=True,timeout=3)
            if out.returncode==0:
                service.update(dict(line.split('=',1) for line in out.stdout.splitlines() if '=' in line))
                started=service.get('ExecMainStartTimestamp') or ''
                if started.endswith(' UTC'):
                    try:service['ExecMainStartTimestamp']=datetime.strptime(started,'%a %Y-%m-%d %H:%M:%S UTC').replace(tzinfo=ZoneInfo('UTC')).astimezone(KST).isoformat()
                    except ValueError:pass
        except (OSError,subprocess.TimeoutExpired):pass
        services.append(service)
        try:
            out=run(['journalctl','-u',unit,'--since','24 hours ago','-n','500','--no-pager','-o','json'],
                    capture_output=True,text=True,timeout=3)
            if out.returncode or re.search(r'permission|not seeing|insufficient|access denied',out.stderr,re.I):
                access='LOG_ACCESS_UNAVAILABLE';continue
            for line in out.stdout.splitlines():
                try:record=json.loads(line)
                except ValueError:continue
                message=redact(str(record.get('MESSAGE','')))
                timestamp=datetime.fromtimestamp(int(record['__REALTIME_TIMESTAMP'])/1000000,KST).isoformat(timespec='seconds')
                if 'FIRST_RISE_REFRESH' in message:last_refresh=max(last_refresh or timestamp,timestamp)
                priority=int(record.get('PRIORITY',6))
                level='ERROR' if priority<=3 or re.search(r'\bERROR\b|Traceback',message) else 'WARNING' if priority==4 or re.search(r'\bWARNING\b',message) else None
                if level:logs.append(dict(time=timestamp,service=unit,level=level,message=message[:6000]))
        except (OSError,subprocess.TimeoutExpired,ValueError,KeyError):access='LOG_ACCESS_UNAVAILABLE'
    logs=sorted(logs,key=lambda row:row['time'],reverse=True)[:50]
    for row in logs:row['copy_text']=log_text(row)
    return dict(services=services,logs=logs,log_access=access,last_refresh=last_refresh)


def lifecycle(row):
    s=row['signal'];c=row.get('cost') or {};intent=row.get('intent') or {}
    evidence=s.get('entry_evidence') or {};capacity=row.get('capacity') or {}
    ev=capacity.get('evidence') or {};buy=c.get('buy_quantity') or 0;sell=c.get('sell_quantity') or 0
    final=c.get('final_net_realized_pnl')
    closed=bool(c.get('provisional_applied_at'))
    if evidence.get('sequence_replay_only'):status='과거신호 복원 — 실주문 없음(정상)'
    elif closed:status='청산 완료'
    elif buy>sell:status='실제 보유'
    else:status=intent.get('planning_reason') or ('실주문 가능 — 주문 없음/대기' if evidence.get('live_entry_eligible') else '시장신호만 추적')
    sell_reasons=[(o.get('trigger') or {}).get('actual_exit_reason') for o in row.get('orders') or [] if o.get('side')=='SELL']
    protection_orders=[o for o in row.get('orders') or [] if o.get('side')=='SELL'
        and (o.get('trigger') or {}).get('actual_exit_reason')=='ACTUAL_STOP_ENTRY_BREAK_PROTECTION']
    protection=protection_orders[-1] if protection_orders else {}
    trigger=protection.get('trigger') or {};observation=protection.get('observation') or {}
    actual_observed=ev.get('actual_exit_observed_at') or observation.get('terminal_observed_at') or observation.get('first_fill_observed_at')
    result=dict(id=s['market_signal_id'],time=s['entry_signal_time'],stock=s['stock_code'],name=row.get('stock_name'),
        sequence='FIRST' if s['signal_sequence']==1 else 'SECOND',status=status,
        signal_price=s['raw_entry_price'],buy_quantity=buy,sell_quantity=sell,
        buy_average=c['buy_amount']/buy if buy else None,sell_average=c['sell_amount']/sell if sell else None,
        market_exit_signal_time=s.get('exit_signal_time'),market_exit_time=s.get('exit_execution_time'),
        market_exit_reason=s.get('exit_reason'),market_exit_price=s.get('raw_exit_price'),
        protection_trigger_time=trigger.get('observation_timestamp'),protection_reference_price=trigger.get('stop_reference_price'),
        protection_observed_price=trigger.get('observed_market_price'),
        actual_sell_order_time=protection.get('broker_created_at') or protection.get('created_at'),
        actual_exit_time=actual_observed,actual_exit_reason=ev.get('actual_exit_reason') or next((r for r in reversed(sell_reasons) if r),None),
        pnl_label='확정 손익' if final is not None else '잠정 손익' if closed else '미실현/미체결',
        net_pnl=final if final is not None else c.get('provisional_net_realized_pnl'),
        evidence=dict(discovered_at=row.get('discovered_at'),entry=evidence,market_exit=s.get('exit_evidence'),
            actual=ev,intent=intent,orders=row.get('orders'),paper=row.get('paper'),shadow=row.get('shadow'),cost=c))
    result=redact(result);result['copy_text']=signal_text(result)
    return result


def attach_orders(rows,links):
    """Attach one batched order result to direct signals and allocated trades."""
    signals={str(row['signal']['market_signal_id']):index for index,row in enumerate(rows)}
    trades={str((row.get('intent') or {}).get('trade_id')):index for index,row in enumerate(rows)
        if (row.get('intent') or {}).get('trade_id') is not None}
    attached=[{} for _ in rows]
    for signal_id,trade_id,order in links:
        targets=set()
        if signal_id is not None and str(signal_id) in signals:targets.add(signals[str(signal_id)])
        if trade_id is not None and str(trade_id) in trades:targets.add(trades[str(trade_id)])
        for index in targets:attached[index][str(order['order_request_id'])]=order
    for index,row in enumerate(rows):
        row['orders']=sorted(attached[index].values(),key=lambda order:(str(order.get('created_at') or ''),str(order['order_request_id'])))
    return rows


def snapshot(pool,day,*,logs=operation_logs):
    with pool.connection() as c,c.transaction(),c.cursor() as q:
        q.execute('SET TRANSACTION READ ONLY')
        q.execute("SET LOCAL statement_timeout='5s'")
        q.execute("""SELECT jsonb_build_object('signal',to_jsonb(s),'stock_name',to_jsonb(e)->>'stock_name',
            'discovered_at',e.discovered_at,'intent',to_jsonb(i),'cost',to_jsonb(c),'capacity',to_jsonb(v),
            'paper',to_jsonb(p),'shadow',to_jsonb(sh))
            FROM first_rise_j_market_signal s
            JOIN first_rise_breakout_candidate_event e USING(candidate_event_id)
            LEFT JOIN first_rise_j_live_intent i ON i.market_signal_id=s.market_signal_id AND i.side='BUY'
            LEFT JOIN first_rise_j_live_cost c ON c.trade_id=i.trade_id
            LEFT JOIN first_rise_j_capacity_observation v ON v.trade_id=i.trade_id
            LEFT JOIN first_rise_j_paper_trade p ON p.market_signal_id=s.market_signal_id
            LEFT JOIN first_rise_j_shadow_trade sh ON sh.market_signal_id=s.market_signal_id
            WHERE s.business_date=%s ORDER BY s.entry_signal_time DESC,s.stock_code""",(day,))
        base_rows=[row[0] for row in q.fetchall()]
        signal_ids=[str(row['signal']['market_signal_id']) for row in base_rows]
        trade_ids=[str(row['intent']['trade_id']) for row in base_rows if row.get('intent') and row['intent'].get('trade_id')]
        order_links=[]
        if signal_ids:
            q.execute("""WITH links AS (
                SELECT r.order_request_id,r.source_decision_id AS market_signal_id,NULL::uuid AS trade_id
                FROM live_order_request r
                WHERE r.strategy_instance_id='FIRST_RISE_J_V1.3'
                  AND r.source_decision_id=ANY(%s::uuid[])
                UNION ALL
                SELECT a.order_request_id,NULL::uuid AS market_signal_id,a.trade_id
                FROM first_rise_j_sell_allocation a
                WHERE a.trade_id=ANY(%s::uuid[]))
                SELECT x.market_signal_id,x.trade_id,jsonb_build_object(
                    'order_request_id',r.order_request_id,'side',r.side,'status',r.status,'created_at',r.created_at,
                    'quantity',r.requested_quantity,'reason',r.reason,'trigger',r.detail,
                    'broker_status',o.status,'broker_created_at',o.created_at,
                    'checkpoint',to_jsonb(f),'observation',to_jsonb(ob))
                FROM links x JOIN live_order_request r USING(order_request_id)
                LEFT JOIN live_broker_order o USING(order_request_id)
                LEFT JOIN first_rise_j_fill_checkpoint f USING(broker_order_id)
                LEFT JOIN first_rise_v2_order_observation ob USING(broker_order_id)
                ORDER BY r.created_at,r.order_request_id""",(signal_ids,trade_ids))
            order_links=q.fetchall()
        rows=[lifecycle(row) for row in attach_orders(base_rows,order_links)]
        q.execute("""SELECT count(*),count(*) FILTER(WHERE entry_evidence->>'sequence_replay_only'='true'),
            count(*) FILTER(WHERE entry_evidence->>'live_entry_eligible'='true'),max(entry_signal_time)
            FROM first_rise_j_market_signal WHERE business_date=%s""",(day,))
        signals,replay,eligible,last_signal=q.fetchone()
        q.execute('SELECT count(*) FROM first_rise_breakout_candidate_event WHERE business_date=%s',(day,));candidates=q.fetchone()[0]
        q.execute('SELECT count(*) FROM first_rise_j_live_cost WHERE buy_quantity>sell_quantity');opened=q.fetchone()[0]
        q.execute("""SELECT count(*) FROM live_order_request WHERE strategy_instance_id='FIRST_RISE_J_V1.3'
            AND status NOT IN ('FILLED','REJECTED','CANCELLED')""");pending=q.fetchone()[0]
        q.execute("""SELECT start_slot_amount+greatest(0,floor(realized_net_pnl/slot_step_amount))*slot_step_amount
            FROM first_rise_j_capital_epoch WHERE ended_at IS NULL""");slot=q.fetchone()
        q.execute("""SELECT count(*),count(*) FILTER(WHERE final_net_realized_pnl IS NOT NULL),
            COALESCE(sum(buy_amount),0),COALESCE(sum(sell_amount),0),
            COALESCE(sum(COALESCE(actual_buy_fee,provisional_buy_fee)+COALESCE(actual_sell_fee,provisional_sell_fee)),0),
            COALESCE(sum(COALESCE(actual_sell_tax,provisional_sell_tax)),0),
            COALESCE(sum(COALESCE(actual_other_cost,provisional_other_cost)),0),
            COALESCE(sum(COALESCE(final_net_realized_pnl,provisional_net_realized_pnl)),0)
            FROM first_rise_j_live_cost WHERE provisional_applied_at::date=%s""",(day,))
        completed,finalized,buy,sell,fee,tax,other,pnl=q.fetchone()
    return dict(date=str(day),generated_at=datetime.now(KST).isoformat(),rows=rows,
        summary=dict(candidates=candidates,signals=signals,replay=replay,eligible=eligible,last_signal=last_signal,
            opened=opened,pending=pending,completed=completed,common_slot=slot[0] if slot else None,
            pnl_label='확정 손익' if completed and completed==finalized else '잠정 손익',net_pnl=pnl,
            finalized_count=finalized,buy_amount=buy,sell_amount=sell,fee=fee,tax=tax,other_cost=other),**logs())
