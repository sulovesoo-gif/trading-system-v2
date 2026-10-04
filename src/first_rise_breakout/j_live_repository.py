"""Atomic FIRST_RISE planning/ownership over the shared order infrastructure."""
from hashlib import sha256
from decimal import Decimal,ROUND_CEILING
from uuid import NAMESPACE_URL,uuid5

from psycopg.types.json import Jsonb

from .j_epoch import JCapitalEpochRepository
from .j_execution import STRATEGY_ID,plan_epoch_entry
from .j_cost import ProvisionalRates


def identity(value):return uuid5(NAMESPACE_URL,'FIRST_RISE|'+value)


class JLiveRepository:
    def __init__(self,pool):self.pool=pool

    def entry_signals(self,*,at):
        with self.pool.connection() as c,c.cursor() as q:
            q.execute('''SELECT s.market_signal_id FROM first_rise_j_market_signal s
                JOIN first_rise_j_activation a ON a.strategy_id=%s
                LEFT JOIN first_rise_j_live_intent i ON i.market_signal_id=s.market_signal_id AND i.side='BUY'
                WHERE i.intent_id IS NULL AND s.entry_signal_time>=a.effective_from
                  AND s.business_date=%s AND s.exit_reason IS NULL
                ORDER BY s.entry_signal_time,s.stock_code''',(STRATEGY_ID,at.date()))
            return [row[0] for row in q.fetchall()]

    def plan_buy(self,signal_id,*,context,at,price_lookup,cash_lookup):
        if context.config is None:return 'INVALID_CONFIG'
        with self.pool.connection() as c,c.transaction(),c.cursor() as q:
            epoch=JCapitalEpochRepository.entry_state(q,epoch_id=context.epoch_id,business_date=at.date())
            q.execute("SELECT intent_id FROM first_rise_j_live_intent WHERE market_signal_id=%s AND side='BUY'",(signal_id,))
            if q.fetchone():return 'DUPLICATE'
            q.execute('''SELECT s.stock_code,s.entry_signal_time,s.signal_sequence,p.exit_reason,p.exit_execution_time,
                a.effective_from,s.exit_reason,s.prior_market_signal_id,s.entry_evidence FROM first_rise_j_market_signal s
                LEFT JOIN first_rise_j_market_signal p ON p.market_signal_id=s.prior_market_signal_id
                JOIN first_rise_j_activation a ON a.strategy_id=%s WHERE s.market_signal_id=%s''',(STRATEGY_ID,signal_id))
            row=q.fetchone()
            if row is None:return 'NO_ACTIVATION'
            stock,signal_time,sequence,prior_reason,prior_time,effective,exited,parent,signal_evidence=row
            if exited is not None:return 'SIGNAL_ALREADY_EXITED'
            if sequence==2:
                # Never allow a parent exception to authorize unrelated lots/orders.
                q.execute('''SELECT 1 FROM live_order_request r WHERE r.strategy_instance_id=%s
                    AND r.execution_stock_code=%s AND r.side='BUY'
                    AND r.status NOT IN ('FILLED','REJECTED','CANCELLED') LIMIT 1''',(STRATEGY_ID,stock))
                if q.fetchone():return 'AWAIT_SAME_STOCK_BUY'
                q.execute('''SELECT 1 FROM first_rise_j_live_cost cost
                    JOIN first_rise_j_capital_binding b ON b.trade_id=cost.trade_id
                    JOIN first_rise_j_live_intent i ON i.trade_id=b.trade_id AND i.side='BUY'
                    WHERE b.stock_code=%s AND cost.buy_quantity>cost.sell_quantity
                      AND i.market_signal_id<>%s LIMIT 1''',(stock,parent))
                if q.fetchone():return 'UNRELATED_SAME_STOCK_POSITION'
                self.cancel_unsubmitted_sells(q,stock=stock)
                if self.pending_stock(q,stock=stock,side='SELL'):return 'AWAIT_SELL_TERMINAL'
            q.execute('''SELECT EXISTS(SELECT 1 FROM live_order_request r WHERE r.strategy_instance_id=%s
                AND r.execution_stock_code=%s AND r.side='BUY' AND r.status NOT IN ('FILLED','REJECTED','CANCELLED'))
                OR EXISTS(SELECT 1 FROM first_rise_j_live_cost cost JOIN first_rise_j_capital_binding b ON b.trade_id=cost.trade_id
                    WHERE b.stock_code=%s AND cost.buy_quantity>cost.sell_quantity)''',(STRATEGY_ID,stock,stock))
            same_stock=q.fetchone()[0]
            q.execute("SELECT COALESCE(sum(reserved_capital),0) FROM live_order_request WHERE strategy_instance_id=%s AND side='BUY' AND status IN ('READY_FOR_BROKER','SUBMITTING','UNKNOWN_BROKER_STATE')",(STRATEGY_ID,))
            reserved=q.fetchone()[0]
            price=price_lookup.current_price(stock)
            cash=cash_lookup.orderable_cash(stock_code=stock,order_price=price,order_division='01').amount
            from .v2_capacity import FORMULA_VERSION
            from .v2_capacity_repository import capacity_day
            v2=signal_evidence.get('formula_version')==FORMULA_VERSION
            capacity=capacity_day(q,at.date()) if v2 else None
            liquidity=signal_evidence.get('entry_liquidity',{'reason':'LIQUIDITY_EVIDENCE_MISSING'}) if v2 else None
            plan=plan_epoch_entry(epoch=epoch,config=context.config,signal_time=signal_time,
                signal_sequence=sequence,prior_exit_reason=prior_reason,prior_exit_time=prior_time,
                effective_from=effective,now=at,broker_cash=cash,price=price,
                buy_fee_rate=ProvisionalRates().buy_fee,same_stock_pending_or_open=same_stock,
                unacknowledged_reservation=reserved,capacity=capacity,liquidity=liquidity)
            evidence={**context.config.evidence(),'broker_cash':str(cash),'reserved_unacknowledged':str(reserved),
                'reference_price':str(price),'common_slot_amount':str(epoch.common_slot_amount),
                'compound_reference':str(epoch.compound_reference),'epoch_id':str(epoch.epoch_id)}
            if v2:
                from .v2_capacity import unbounded_slot
                evidence.update(formula_version=FORMULA_VERSION,common_slot_amount=str(unbounded_slot(context.config,epoch.realized_net_pnl)))
                evidence.update(plan.sizing_evidence or {'reason':plan.reason})
            evidence.update(market_signal_id=str(signal_id),requested_quantity=plan.quantity,estimated_cash_used=str(plan.cash_required))
            trade=None;request=None
            intent=identity(str(signal_id)+'|BUY')
            if plan.quantity>0:
                trade=identity(str(signal_id)+'|TRADE')
                JCapitalEpochRepository.bind_entry(q,trade_id=trade,expected_epoch_id=epoch.epoch_id,
                    business_date=at.date(),stock_code=stock,sizing_evidence=evidence,at=at)
                request=self._request(q,signal_id=signal_id,intent=intent,trade=trade,stock=stock,side='BUY',
                    quantity=plan.quantity,price=price,cash=plan.cash_required,at=at,evidence=evidence)
            q.execute('''INSERT INTO first_rise_j_live_intent(intent_id,market_signal_id,trade_id,side,
                signal_time,order_request_id,planning_reason,sizing_evidence) VALUES(%s,%s,%s,'BUY',%s,%s,%s,%s)''',
                (intent,signal_id,trade,signal_time,request,plan.reason,Jsonb(evidence)))
            return plan.reason

    @staticmethod
    def _request(q,*,signal_id,intent,trade,stock,side,quantity,price,cash,at,evidence,generation=0):
        key=sha256(f'{STRATEGY_ID}|{signal_id}|{side}|{generation}'.encode()).hexdigest()
        request=identity(key)
        detail={**evidence,'first_rise_trade_id':str(trade),'market_signal_id':str(signal_id)}
        cash=Decimal(cash).quantize(Decimal('.01'),rounding=ROUND_CEILING)
        q.execute('''INSERT INTO live_order_request(order_request_id,idempotency_key,strategy_instance_id,
            source_intent_id,source_decision_id,execution_stock_code,side,requested_notional,requested_quantity,
            reference_price,order_type,execution_target_time,strategy_capital_before,reserved_capital,
            safety_status,status,reason,detail) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'MARKET',%s,%s,%s,
            'VALIDATED','READY_FOR_BROKER','FIRST_RISE_J',%s)''',
            (request,key,STRATEGY_ID,intent,signal_id,stock,side,quantity*price,quantity,price,at,
             Decimal(evidence.get('common_slot_amount','0')),cash,Jsonb(detail)))
        return request

    def plan_exits(self,*,at):
        count=0
        with self.pool.connection() as c,c.transaction(),c.cursor() as q:
            q.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_j_capital_epoch'))")
            q.execute('''SELECT DISTINCT b.stock_code FROM first_rise_j_live_cost c
                JOIN first_rise_j_capital_binding b ON b.trade_id=c.trade_id
                WHERE c.buy_quantity>c.sell_quantity ORDER BY b.stock_code''')
            for (stock,) in q.fetchall():
                if self.pending_stock(q,stock=stock):continue
                q.execute('''SELECT s.market_signal_id,s.exit_signal_time,s.raw_exit_price,s.exit_reason,
                    s.signal_sequence,i.planning_reason,i.order_request_id,s.entry_signal_time
                    FROM first_rise_j_market_signal s LEFT JOIN first_rise_j_live_intent i
                      ON i.market_signal_id=s.market_signal_id AND i.side='BUY'
                    WHERE s.stock_code=%s ORDER BY s.entry_signal_time DESC LIMIT 1''',(stock,))
                event=q.fetchone()
                if event is None:continue
                signal,signal_time,price,reason,sequence,planning,buy_request,entry_time=event
                if reason is None:
                    # SECOND market lifecycle, not actual BUY success, owns the
                    # transition. Keep FIRST residual until SECOND independent EXIT.
                    if sequence==2:continue
                    q.execute('''SELECT s.market_signal_id,s.exit_signal_time,s.raw_exit_price FROM first_rise_j_market_signal s
                        JOIN first_rise_j_live_intent i ON i.market_signal_id=s.market_signal_id AND i.side='BUY'
                        JOIN first_rise_j_live_cost c ON c.trade_id=i.trade_id
                        WHERE s.stock_code=%s AND s.exit_reason IS NOT NULL AND c.buy_quantity>c.sell_quantity
                        ORDER BY s.entry_signal_time DESC LIMIT 1''',(stock,))
                    old_exit=q.fetchone()
                    if old_exit is None:continue
                    signal,signal_time,price=old_exit
                    if signal_time is None:continue
                # No verified KIS retry allowlist exists. Never infer rePOST.
                q.execute('''SELECT r.status,r.detail FROM first_rise_j_live_intent i
                    JOIN live_order_request r ON r.order_request_id=i.order_request_id
                    WHERE i.market_signal_id=%s AND i.side='SELL' ORDER BY i.generation DESC LIMIT 1''',(signal,))
                previous=q.fetchone()
                if previous and previous[0]=='REJECTED':continue
                q.execute('''SELECT b.trade_id,b.epoch_id,c.buy_quantity-c.sell_quantity
                    FROM first_rise_j_live_cost c JOIN first_rise_j_capital_binding b ON b.trade_id=c.trade_id
                    JOIN first_rise_j_live_intent i ON i.trade_id=b.trade_id AND i.side='BUY'
                    WHERE b.stock_code=%s AND c.buy_quantity>c.sell_quantity
                    ORDER BY i.signal_time,b.trade_id''',(stock,))
                lots=q.fetchall()
                if not lots:continue
                trade=lots[0][0];qty=sum(row[2] for row in lots)
                q.execute("SELECT COALESCE(max(generation)+1,0) FROM first_rise_j_live_intent WHERE market_signal_id=%s AND side='SELL'",(signal,))
                generation=q.fetchone()[0]
                intent=identity(str(signal)+'|SELL|'+str(generation))
                request=self._request(q,signal_id=signal,intent=intent,trade=trade,stock=stock,side='SELL',
                    quantity=int(qty),price=price,cash=0,at=at,evidence={'ownership':'FIRST_RISE','exit_state':'EXIT_RECOVERY'},generation=generation)
                q.execute('''INSERT INTO first_rise_j_live_intent(intent_id,market_signal_id,trade_id,side,signal_time,order_request_id,generation)
                    VALUES(%s,%s,%s,'SELL',%s,%s,%s)''',(intent,signal,trade,signal_time,request,generation))
                for index,(lot,epoch,quantity) in enumerate(lots):
                    q.execute('''INSERT INTO first_rise_j_sell_allocation(order_request_id,trade_id,epoch_id,allocation_order,planned_quantity)
                        VALUES(%s,%s,%s,%s,%s)''',(request,lot,epoch,index,quantity))
                count+=1
        return count

    @staticmethod
    def pending_stock(q,*,stock,side=None):
        q.execute('''SELECT 1 FROM live_order_request WHERE strategy_instance_id=%s AND execution_stock_code=%s
            AND (%s::text IS NULL OR side=%s) AND status NOT IN ('FILLED','REJECTED','CANCELLED') LIMIT 1''',
            (STRATEGY_ID,stock,side,side))
        return q.fetchone() is not None

    @staticmethod
    def cancel_unsubmitted_sells(q,*,stock):
        q.execute('''UPDATE live_order_request r SET status='CANCELLED',reason='SECOND_SUPERSEDES_UNSENT_SELL'
            WHERE r.strategy_instance_id=%s AND r.execution_stock_code=%s AND r.side='SELL'
            AND r.status IN ('READY_FOR_BROKER','SUBMITTING')
            AND NOT EXISTS(SELECT 1 FROM live_broker_order o JOIN live_broker_order_audit a ON a.broker_order_id=o.broker_order_id
                WHERE o.order_request_id=r.order_request_id AND a.event_type='FIRST_RISE_POST_ATTEMPT')
            RETURNING r.order_request_id''',(STRATEGY_ID,stock))
        for (request,) in q.fetchall():
            q.execute("UPDATE live_broker_order SET status='CANCELLED' WHERE order_request_id=%s AND status='SUBMITTING'",(request,))
