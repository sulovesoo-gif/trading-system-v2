-- V1.9: immutable evidence of FIRST_RISE's actual completed REST input.
-- No modification of raw_stock_minute or historical PAPER/market ledgers.
BEGIN;
CREATE TABLE first_rise_completed_minute_raw (
 business_date date NOT NULL,
 stock_code varchar(20) NOT NULL,
 bar_time timestamp NOT NULL,
 source text NOT NULL DEFAULT 'KIS_FHKST03010200' CHECK(source='KIS_FHKST03010200'),
 tr_id text NOT NULL DEFAULT 'FHKST03010200' CHECK(tr_id='FHKST03010200'),
 open_price numeric NOT NULL,high_price numeric NOT NULL,
 low_price numeric NOT NULL,close_price numeric NOT NULL,
 volume bigint NOT NULL,accumulated_amount numeric,
 previous_close_price numeric,
 fetched_at timestamp NOT NULL,
 observed_as_of timestamp NOT NULL,
 collection_mode text NOT NULL CHECK(collection_mode IN ('bootstrap','incremental','catch_up')),
 provenance text NOT NULL DEFAULT 'RUNTIME' CHECK(provenance='RUNTIME'),
 raw_payload jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(business_date,stock_code,bar_time,source),
 CHECK(bar_time::date=business_date),
 CHECK(bar_time::time BETWEEN TIME '09:00' AND TIME '15:30'),
 CHECK(bar_time=date_trunc('minute',bar_time)),
 CHECK(bar_time<date_trunc('minute',observed_as_of)),
 CHECK(open_price>0 AND low_price>0 AND high_price>=low_price AND close_price>0),
 CHECK(volume>=0)
);
COMMENT ON TABLE first_rise_completed_minute_raw IS
 'First-seen completed KIS REST bars actually supplied to FIRST_RISE. Insert-only; terminal cache eviction never deletes rows. Post-hoc replay must not be labelled RUNTIME.';
COMMIT;
