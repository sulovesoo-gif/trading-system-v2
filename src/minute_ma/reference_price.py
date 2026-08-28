from datetime import datetime
from decimal import Decimal

from src.collector.raw.converters import combine_kst_datetime

class MinuteMaKISReferencePriceLookup:
    path="/uapi/domestic-stock/v1/quotations/inquire-price"
    tr_id="FHKST01010100"
    def __init__(self,client):self.client=client
    def current_price(self,stock_code):
        payload=self.client.get(path=self.path,tr_id=self.tr_id,params={"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":stock_code})
        raw=str((payload.get('output') or {}).get('stck_prpr') or '').strip()
        if not raw or Decimal(raw)<=0:raise ValueError('MINUTE_MA_REFERENCE_PRICE_REQUIRED')
        return Decimal(raw)

    def minute_open(self,stock_code:str,bar_time:datetime):
        """Return the broker-observed KRX OPEN for one exact entry minute."""
        payload=self.client.get(
            path="/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            tr_id="FHKST03010200",
            params={"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":stock_code,
                    "FID_INPUT_HOUR_1":bar_time.strftime('%H%M%S'),
                    "FID_PW_DATA_INCU_YN":"Y","FID_ETC_CLS_CODE":""})
        target=bar_time.replace(second=0,microsecond=0);matches=[]
        for row in payload.get('output2') or ():
            observed=combine_kst_datetime(row.get('stck_bsop_date'),row.get('stck_cntg_hour'),
                                          collection_time=bar_time)
            if observed==target:
                raw=str(row.get('stck_oprc') or '').strip()
                if raw and Decimal(raw)>0:matches.append(Decimal(raw))
        if len(matches)!=1:raise ValueError('MINUTE_MA_UNDERLYING_ENTRY_OPEN_REQUIRED')
        return matches[0]
