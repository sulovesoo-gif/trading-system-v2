"""FLOW KIS read-only adapters and physically disabled order boundary."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from .live_contract import request_payload


def kst_now():
    return datetime.now(ZoneInfo('Asia/Seoul')).replace(tzinfo=None)


class FlowBrokerReader:
    def __init__(self, client, account, clock=kst_now):
        self.client,self.account,self.clock=client,account,clock

    def quotes(self):
        result={}
        for code in ('0193T0','0197X0'):
            response=self.client.get(path='/uapi/domestic-stock/v1/quotations/inquire-price',
                tr_id='FHKST01010100',params={'FID_COND_MRKT_DIV_CODE':'J','FID_INPUT_ISCD':code})
            price=Decimal(str((response.get('output') or {}).get('stck_prpr') or '0'))
            if not price.is_finite() or price<=0:
                raise ValueError('FLOW_KRX_QUOTE_MISSING:'+code)
            result[code]=(price,self.clock())
        return result

    def poll(self, repository):
        # Existing history parser is read-only and remains unmodified. Never
        # match unknown orders by quantity/time: independent strategies can be identical.
        from src.daily_ma_v03.kis_order_history import DailyMaKISOrderHistoryLookup
        history=DailyMaKISOrderHistoryLookup(client=self.client,account=self.account)
        count=0
        for o in repository.orders_to_poll():
            records=history.orders_for_day(order_date=o['broker_order_date'],stock_code=o['execution_code'],
                side=o['side'],order_number=o['broker_order_number'])
            records=[r for r in records if r.order_number==o['broker_order_number']
                     and r.order_date==o['broker_order_date'].strftime('%Y%m%d')]
            if len(records)!=1:
                repository.record_error('BROKER_HISTORY_NOT_UNIQUE:'+str(o['broker_order_id']))
                continue
            r=records[0]
            status=('FILLED' if r.total_filled_quantity==r.order_quantity else
                    'CANCELLED' if r.cancelled and r.remaining_quantity==0 else 'REJECTED' if r.rejected_quantity==r.order_quantity else
                    'PARTIAL' if r.total_filled_quantity else 'ACK')
            repository.observe(o['broker_order_id'],order_number=r.order_number,
                order_date=o['broker_order_date'],stock_code=r.stock_code,side=r.side,
                requested_quantity=r.order_quantity,filled_quantity=r.total_filled_quantity,
                filled_amount=r.total_filled_amount,status=status,observed_at=self.clock(),remaining_quantity=r.remaining_quantity)
            count+=1
        for r in repository.pending_cancellations():
            # Official KIS contract requires this inquiry before a cancel. No
            # result (including a paginated miss) is NOT proof of cancellation.
            matches=self.cancellable_order(r['broker_order_number'],r['execution_code'])
            if len(matches)==1 and int(matches[0].get('psbl_qty') or 0)>0:
                repository.prepare_cancel(r['entry_intent_id'],number=r['broker_order_number'],
                    branch=str(matches[0].get('ord_gno_brno') or ''),
                    cancellable_quantity=int(matches[0]['psbl_qty']),observed_at=self.clock())
        return count

    def cancellable_order(self,number,code):
        fk=nk='';matches=[]
        for page in range(10):
            payload=self.client.get(path='/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl',tr_id='TTTC0084R',
                params={'CANO':self.account.cano,'ACNT_PRDT_CD':self.account.account_product_code,
                        'CTX_AREA_FK100':fk,'CTX_AREA_NK100':nk,'INQR_DVSN_1':'1','INQR_DVSN_2':'2'},
                extra_headers={'tr_cont':'N'} if page else None)
            matches.extend(x for x in payload.get('output',[]) if str(x.get('odno','')).strip()==number
                           and str(x.get('pdno','')).strip()==code)
            if self.client.last_response_headers.get('tr_cont') not in ('M','F'):
                return matches
            fk=str(payload.get('ctx_area_fk100') or '');nk=str(payload.get('ctx_area_nk100') or '')
        raise ValueError('FLOW_CANCEL_INQUIRY_PAGINATION_INCOMPLETE')


class NoSendBoundary:
    actual_post_count = 0

    @staticmethod
    def prepare(code, side, quantity):
        return request_payload(code,side,quantity)

    @staticmethod
    def submit(*args, **kwargs):
        raise PermissionError('FLOW_ACTUAL_SEND_PHYSICALLY_DISABLED')
