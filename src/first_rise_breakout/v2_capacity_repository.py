"""V2 observation ledger, independent of capital settlement and broker submit."""
import logging
from decimal import Decimal
from psycopg.types.json import Jsonb
from .v2_capacity import CapacityConfig,shadow_result,rolling_comparison,SHADOW_VERSION,FORMULA_VERSION

LOGGER=logging.getLogger(__name__)


def capacity_day(q,day):
    q.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_capacity_daily'))")
    q.execute('SELECT config_row,config_error FROM first_rise_capacity_day WHERE business_date=%s',(day,))
    saved=q.fetchone()
    if saved is None:
        q.execute("SELECT use_yn,attr1,attr2,attr3,attr4,attr5,attr6,attr7 FROM common_code WHERE group_cd='FIRST_RISE_CAPACITY' AND code='DEFAULT'")
        rows=q.fetchall();row=list(rows[0]) if len(rows)==1 else [];error=None
        try:CapacityConfig.from_row(row)
        except ValueError as e:error=str(e)
        q.execute('INSERT INTO first_rise_capacity_day(business_date,config_row,config_error) VALUES(%s,%s,%s)',(day,Jsonb(row),error))
    else:row,error=saved
    if error:
        LOGGER.error('FIRST_RISE_CAPACITY_CONFIG_ERROR date=%s error=%s',day,error)
        return None
    return CapacityConfig.from_row(row)


def shadow_entry(q,signal_id,decision):
    if (decision.evidence or {}).get('formula_version')!=FORMULA_VERSION:return
    config=capacity_day(q,decision.signal_time.date())
    # Shadow fixed amount is invariant, even if ENTRY capacity config is invalid.
    plan=shadow_result(raw_entry=decision.raw_execution_price)
    evidence=config.evidence() if config else {'config_error':'INVALID_CAPACITY_CONFIG'}
    q.execute('''INSERT INTO first_rise_j_shadow_trade(market_signal_id,contract,fixed_amount,
        quantity,cash_used,entry_time,config_evidence) VALUES(%s,%s,10000000,%s,%s,%s,%s)
        ON CONFLICT(market_signal_id) DO NOTHING''',
        (signal_id,SHADOW_VERSION,plan['shadow_quantity'],Decimal(plan['shadow_cash_used']),decision.signal_time,Jsonb(evidence)))


def shadow_exit(q,signal_id,decision):
    q.execute('''SELECT s.raw_entry_price FROM first_rise_j_shadow_trade sh
        JOIN first_rise_j_market_signal s USING(market_signal_id)
        WHERE sh.market_signal_id=%s AND sh.exit_time IS NULL''',(signal_id,))
    row=q.fetchone()
    if row is None:return
    plan=shadow_result(raw_entry=row[0],raw_exit=decision.raw_execution_price)
    q.execute('''UPDATE first_rise_j_shadow_trade SET exit_time=%s,exit_reason=%s,net_pnl=%s,net_return=%s
        WHERE market_signal_id=%s AND exit_time IS NULL''',
        (decision.after.last_observed_at,decision.reason,plan['shadow_net_pnl'] or '0',plan['shadow_net_return'],signal_id))


class CapacityMonitor:
    def __init__(self,pool,notifier=None):self.pool,self.notifier=pool,notifier

    def refresh(self,*,at):
        with self.pool.connection() as c,c.transaction(),c.cursor() as q:
            # Only V2 signals that have Shadow rows are eligible; no historical backfill.
            q.execute('''SELECT i.trade_id,s.market_signal_id,s.stock_code,s.signal_sequence,s.entry_signal_time,
                s.exit_execution_time,s.exit_reason,sh.net_return,c.buy_amount,c.sell_amount,
                c.buy_quantity,c.sell_quantity,c.provisional_buy_fee,c.actual_buy_fee,
                c.provisional_net_realized_pnl,c.final_net_realized_pnl,c.provisional_applied_at,
                i.sizing_evidence,s.exit_evidence,s.raw_entry_price,s.raw_exit_price
                FROM first_rise_j_shadow_trade sh JOIN first_rise_j_market_signal s USING(market_signal_id)
                JOIN first_rise_j_live_intent i ON i.market_signal_id=s.market_signal_id AND i.side='BUY'
                JOIN first_rise_j_live_cost c ON c.trade_id=i.trade_id WHERE c.buy_quantity>0''')
            for row in q.fetchall():
                trade,signal,stock,seq,entry,exit_at,reason,shadow,buy,sell,bq,sq,pfee,afee,pnl,final,closed,sizing,exit_ev,raw_entry,raw_exit=row
                provisional=pnl/(buy+(pfee or 0)) if pnl is not None else None
                final_return=final/(buy+(afee or 0)) if final is not None else None
                ev=dict(sizing)
                q.execute('''SELECT o.side,bool_or(t.had_partial_fill),min(r.execution_target_time),
                    max(t.terminal_observed_at),count(*) FILTER (WHERE t.terminal_observed_at IS NULL)
                    FROM live_broker_order o JOIN live_order_request r USING(order_request_id)
                    JOIN first_rise_v2_order_observation t USING(broker_order_id)
                    WHERE r.detail->>'first_rise_trade_id'=%s OR EXISTS (
                        SELECT 1 FROM first_rise_j_sell_allocation a WHERE a.order_request_id=r.order_request_id AND a.trade_id=%s)
                    GROUP BY o.side''',(str(trade),trade))
                for side,partial,started,finished,pending in q.fetchall():
                    prefix=side.lower()
                    ev[prefix+'_partial_fill_yn']=bool(partial)
                    ev[prefix+'_fill_completion_observed_at']=finished.isoformat() if finished and not pending else None
                    ev[prefix+'_fill_duration_ms']=int((finished-started).total_seconds()*1000) if finished and not pending else None
                ev['fill_time_basis']='KIS_CUMULATIVE_POLL_OBSERVATION_NOT_EXACT_EXECUTION_TIME'
                ev['live_closed_at']=closed.isoformat() if closed else None
                entry_amount=ev.get('recent_5m_traded_amount')
                exit_amount=((exit_ev or {}).get('exit_liquidity') or {}).get('recent_5m_traded_amount')
                ev.update(live_invested_cash=str(buy),actual_buy_amount=str(buy),actual_sell_amount=str(sell),
                    entry_actual_participation_pct=str(buy/Decimal(entry_amount)*100) if entry_amount else None,
                    exit_recent_5m_amount=exit_amount,
                    exit_actual_participation_pct=str(sell/Decimal(exit_amount)*100) if exit_amount else None,
                    entry_slippage_bps_vs_shadow=str((buy/bq/(raw_entry*Decimal('1.0002'))-1)*10000),
                    exit_slippage_bps_vs_shadow=str((sell/sq/(raw_exit*Decimal('.9998'))-1)*10000) if sq and raw_exit else None,
                    return_gap_bp=str(((final_return if final_return is not None else provisional)-shadow)*10000)
                        if shadow is not None and provisional is not None else None)
                status='FINAL' if final is not None else 'PROVISIONAL' if closed else 'OPEN'
                q.execute('''INSERT INTO first_rise_j_capacity_observation(trade_id,market_signal_id,stock_code,signal_sequence,
                    entry_time,exit_time,exit_reason,comparison_status,shadow_net_return,live_return_provisional,live_return_final,evidence)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(trade_id) DO UPDATE SET
                    exit_time=EXCLUDED.exit_time,exit_reason=EXCLUDED.exit_reason,comparison_status=EXCLUDED.comparison_status,
                    shadow_net_return=EXCLUDED.shadow_net_return,live_return_provisional=EXCLUDED.live_return_provisional,
                    live_return_final=EXCLUDED.live_return_final,evidence=EXCLUDED.evidence,updated_at=CURRENT_TIMESTAMP''',
                    (trade,signal,stock,seq,entry,exit_at,reason,status,shadow,provisional,final_return,Jsonb(ev)))
            config=capacity_day(q,at.date())
            if config is not None:self._warnings(q,config)
        self.deliver()

    @staticmethod
    def _warnings(q,config):
        q.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_capacity_warnings'))")
        q.execute('''SELECT shadow_net_return,COALESCE(live_return_final,live_return_provisional)
            FROM first_rise_j_capacity_observation WHERE comparison_status IN ('PROVISIONAL','FINAL')
            AND shadow_net_return IS NOT NULL ORDER BY (evidence->>'live_closed_at')::timestamp DESC,trade_id DESC LIMIT %s''',(config.long_window,))
        samples=list(reversed(q.fetchall()))
        names=('NORMAL','WARNING','STRONG_WARNING','CRITICAL_REVIEW')
        for window in (config.short_window,config.long_window):
            result=rolling_comparison(samples,count=window,config=config);severity=names.index(result['level'])
            q.execute('SELECT severity,revision FROM first_rise_capacity_warning WHERE window_size=%s FOR UPDATE',(window,))
            old=q.fetchone();previous,revision=old if old else (0,0)
            revision+=int(severity!=previous)
            q.execute('''INSERT INTO first_rise_capacity_warning(window_size,severity,revision,evidence) VALUES(%s,%s,%s,%s)
                ON CONFLICT(window_size) DO UPDATE SET severity=EXCLUDED.severity,revision=EXCLUDED.revision,
                evidence=EXCLUDED.evidence,updated_at=CURRENT_TIMESTAMP''',(window,severity,revision,Jsonb(result)))
            if severity>previous:
                q.execute('''INSERT INTO first_rise_capacity_alert(window_size,revision,evidence) VALUES(%s,%s,%s)
                    ON CONFLICT(window_size,revision) DO NOTHING''',(window,revision,Jsonb(result)))
                LOGGER.warning('FIRST_RISE_CAPACITY_WARNING window=%s result=%s',window,result)

    def deliver(self):
        # Durable at-most-once claim; an uncertain notification is not retransmitted.
        with self.pool.connection() as c,c.transaction(),c.cursor() as q:
            q.execute('''UPDATE first_rise_capacity_alert SET delivery_attempted_at=CURRENT_TIMESTAMP,delivery_status='ATTEMPTED'
                WHERE event_id IN (SELECT event_id FROM first_rise_capacity_alert WHERE delivery_attempted_at IS NULL
                    FOR UPDATE SKIP LOCKED) RETURNING event_id,evidence''')
            events=q.fetchall()
        for event,evidence in events:
            status='LOG_ONLY'
            if self.notifier is not None:
                try:self.notifier.send(subject='FIRST_RISE capacity',body=str(evidence));status='SENT'
                except Exception:status='UNKNOWN';LOGGER.exception('FIRST_RISE_CAPACITY_ALERT_UNCERTAIN event=%s',event)
            with self.pool.connection() as c,c.transaction(),c.cursor() as q:
                q.execute('UPDATE first_rise_capacity_alert SET delivery_status=%s WHERE event_id=%s',(status,event))
