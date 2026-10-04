"""Insert-only completed-bar evidence; no shared RAW writes or retention deletes."""
import logging
from datetime import datetime, time
from psycopg.types.json import Jsonb

LOGGER = logging.getLogger(__name__)
SOURCE = 'KIS_FHKST03010200'
FIELDS = ('bar_time','stock_code','open_price','high_price','low_price','close_price',
          'volume','accumulated_amount','previous_close_price','raw_payload','fetched_at','observed_as_of','collection_mode')


class FirstRiseMinuteRawRepository:
    def __init__(self, pool):
        self.pool = pool

    def load(self, *, stock_code, as_of):
        with self.pool.connection() as c, c.cursor() as q:
            q.execute('SELECT '+','.join(FIELDS)+''' FROM first_rise_completed_minute_raw
                WHERE business_date=%s AND stock_code=%s AND source=%s AND bar_time<%s
                ORDER BY bar_time''',
                (as_of.date(),stock_code,SOURCE,as_of.replace(second=0,microsecond=0)))
            return [dict(zip(FIELDS,row)) for row in q.fetchall()]

    def preserve(self, *, stock_code, as_of, rows, mode):
        """Commit before evaluation. Return canonical first-seen rows on replay.

        A changed REST response never silently rewrites evidence or changes the
        cached bar. It is logged; the strategy continues to use the saved bar.
        """
        result=[]
        with self.pool.connection() as c, c.transaction(), c.cursor() as q:
            for row in sorted(rows,key=lambda r:r['bar_time']):
                at=row['bar_time']
                if (at.date()!=as_of.date() or not time(9)<=at.time()<=time(15,30)
                        or at>=as_of.replace(second=0,microsecond=0)):
                    continue
                if row.get('stock_code',stock_code)!=stock_code:
                    raise ValueError('FIRST_RISE_RAW_STOCK_MISMATCH')
                fetched=row.get('collected_at') or row.get('fetched_at') or as_of
                values=[row.get(key) for key in FIELDS[2:9]]
                payload=row.get('raw_payload') or {}
                q.execute('''INSERT INTO first_rise_completed_minute_raw
                    (business_date,stock_code,bar_time,source,open_price,high_price,low_price,close_price,
                     volume,accumulated_amount,previous_close_price,fetched_at,observed_as_of,collection_mode,raw_payload)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(business_date,stock_code,bar_time,source) DO NOTHING''',
                    (as_of.date(),stock_code,at,SOURCE,*values,fetched,as_of,mode,Jsonb(payload)))
                q.execute('SELECT '+','.join(FIELDS)+''' FROM first_rise_completed_minute_raw
                    WHERE business_date=%s AND stock_code=%s AND bar_time=%s AND source=%s''',
                    (as_of.date(),stock_code,at,SOURCE))
                saved=dict(zip(FIELDS,q.fetchone()))
                if any(saved[key]!=row.get(key) for key in FIELDS[2:9]):
                    LOGGER.error('FIRST_RISE_RAW_REVISION stock_code=%s bar_time=%s original_preserved=true',stock_code,at)
                result.append(saved)
        return result
