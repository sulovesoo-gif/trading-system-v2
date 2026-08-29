BEGIN;

CREATE TABLE IF NOT EXISTS flow_ws_connection (
    connection_id UUID PRIMARY KEY,
    collector_instance_id UUID NOT NULL,
    connected_at TIMESTAMP(6) NOT NULL,
    disconnected_at TIMESTAMP(6),
    reconnect_flag BOOLEAN NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('CONNECTED','DISCONNECTED','FAILED')),
    subscriptions JSONB NOT NULL,
    last_receive_sequence BIGINT NOT NULL DEFAULT 0,
    close_reason TEXT,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw_flow_execution (
    received_at TIMESTAMP(6) NOT NULL,
    source_event_time TIMESTAMP(6) NOT NULL,
    business_date DATE NOT NULL,
    stock_code VARCHAR(20) NOT NULL,
    trading_venue VARCHAR(10) NOT NULL CHECK (trading_venue='KRX'),
    tr_id VARCHAR(10) NOT NULL CHECK (tr_id='H0STCNT0'),
    connection_id UUID NOT NULL REFERENCES flow_ws_connection(connection_id),
    collector_instance_id UUID NOT NULL,
    receive_sequence BIGINT NOT NULL,
    event_index SMALLINT NOT NULL,
    reconnect_flag BOOLEAN NOT NULL,
    source_gap_flag BOOLEAN NOT NULL,
    event_time_regression_flag BOOLEAN NOT NULL,
    duplicate_flag BOOLEAN NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    current_price BIGINT,
    execution_volume BIGINT,
    accumulated_volume BIGINT,
    accumulated_amount NUMERIC(24,2),
    sell_execution_count BIGINT,
    buy_execution_count BIGINT,
    net_buy_execution_count BIGINT,
    execution_strength NUMERIC(18,6),
    total_sell_quantity BIGINT,
    total_buy_quantity BIGINT,
    execution_classification VARCHAR(10),
    buy_ratio NUMERIC(18,6),
    ask_price_1 BIGINT,
    bid_price_1 BIGINT,
    ask_quantity_1 BIGINT,
    bid_quantity_1 BIGINT,
    total_ask_quantity BIGINT,
    total_bid_quantity BIGINT,
    raw_values JSONB NOT NULL,
    raw_payload TEXT NOT NULL,
    raw_source VARCHAR(30) NOT NULL DEFAULT 'KIS_WEBSOCKET',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (received_at, connection_id, receive_sequence, event_index)
);
SELECT create_hypertable('raw_flow_execution','received_at',if_not_exists=>TRUE);
CREATE INDEX IF NOT EXISTS idx_raw_flow_execution_code_event
    ON raw_flow_execution(stock_code, source_event_time DESC);
CREATE INDEX IF NOT EXISTS idx_raw_flow_execution_hash
    ON raw_flow_execution(payload_hash, received_at DESC);

CREATE TABLE IF NOT EXISTS raw_flow_program (
    received_at TIMESTAMP(6) NOT NULL,
    source_event_time TIMESTAMP(6) NOT NULL,
    business_date DATE NOT NULL,
    stock_code VARCHAR(20) NOT NULL,
    trading_venue VARCHAR(10) NOT NULL CHECK (trading_venue='KRX'),
    tr_id VARCHAR(10) NOT NULL CHECK (tr_id='H0STPGM0'),
    connection_id UUID NOT NULL REFERENCES flow_ws_connection(connection_id),
    collector_instance_id UUID NOT NULL,
    receive_sequence BIGINT NOT NULL,
    event_index SMALLINT NOT NULL,
    reconnect_flag BOOLEAN NOT NULL,
    source_gap_flag BOOLEAN NOT NULL,
    event_time_regression_flag BOOLEAN NOT NULL,
    duplicate_flag BOOLEAN NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    sell_execution_quantity BIGINT,
    sell_execution_amount NUMERIC(24,2),
    buy_execution_quantity BIGINT,
    buy_execution_amount NUMERIC(24,2),
    net_buy_execution_quantity BIGINT,
    net_buy_execution_amount NUMERIC(24,2),
    sell_orderbook_quantity BIGINT,
    buy_orderbook_quantity BIGINT,
    total_net_buy_orderbook_quantity BIGINT,
    raw_values JSONB NOT NULL,
    raw_payload TEXT NOT NULL,
    raw_source VARCHAR(30) NOT NULL DEFAULT 'KIS_WEBSOCKET',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (received_at, connection_id, receive_sequence, event_index)
);
SELECT create_hypertable('raw_flow_program','received_at',if_not_exists=>TRUE);
CREATE INDEX IF NOT EXISTS idx_raw_flow_program_code_event
    ON raw_flow_program(stock_code, source_event_time DESC);
CREATE INDEX IF NOT EXISTS idx_raw_flow_program_hash
    ON raw_flow_program(payload_hash, received_at DESC);

CREATE TABLE IF NOT EXISTS raw_flow_orderbook_5s (
    bucket_start TIMESTAMP(0) NOT NULL,
    source_event_time TIMESTAMP(6) NOT NULL,
    received_at TIMESTAMP(6) NOT NULL,
    stock_code VARCHAR(20) NOT NULL,
    trading_venue VARCHAR(10) NOT NULL CHECK (trading_venue='KRX'),
    tr_id VARCHAR(10) NOT NULL CHECK (tr_id='H0STASP0'),
    connection_id UUID NOT NULL REFERENCES flow_ws_connection(connection_id),
    collector_instance_id UUID NOT NULL,
    receive_sequence BIGINT NOT NULL,
    reconnect_flag BOOLEAN NOT NULL,
    source_gap_flag BOOLEAN NOT NULL,
    event_time_regression_flag BOOLEAN NOT NULL,
    duplicate_flag BOOLEAN NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    ask_prices BIGINT[] NOT NULL,
    bid_prices BIGINT[] NOT NULL,
    ask_quantities BIGINT[] NOT NULL,
    bid_quantities BIGINT[] NOT NULL,
    total_ask_quantity BIGINT,
    total_bid_quantity BIGINT,
    total_ask_quantity_change BIGINT,
    total_bid_quantity_change BIGINT,
    midpoint NUMERIC(24,6),
    raw_values JSONB NOT NULL,
    raw_payload TEXT NOT NULL,
    raw_source VARCHAR(30) NOT NULL DEFAULT 'KIS_WEBSOCKET',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (bucket_start, stock_code, trading_venue)
);
SELECT create_hypertable('raw_flow_orderbook_5s','bucket_start',if_not_exists=>TRUE);
CREATE INDEX IF NOT EXISTS idx_raw_flow_orderbook_code_time
    ON raw_flow_orderbook_5s(stock_code, bucket_start DESC);

CREATE TABLE IF NOT EXISTS flow_bar (
    bucket_start TIMESTAMP(0) NOT NULL,
    bucket_end TIMESTAMP(0) NOT NULL,
    interval_seconds SMALLINT NOT NULL CHECK (interval_seconds IN (5,60)),
    stock_code VARCHAR(20) NOT NULL,
    trading_venue VARCHAR(10) NOT NULL CHECK (trading_venue='KRX'),
    event_count BIGINT NOT NULL DEFAULT 0,
    aggressive_buy_notional NUMERIC(24,2) NOT NULL DEFAULT 0,
    aggressive_sell_notional NUMERIC(24,2) NOT NULL DEFAULT 0,
    net_aggressive_notional NUMERIC(24,2) NOT NULL DEFAULT 0,
    buy_trade_count BIGINT NOT NULL DEFAULT 0,
    sell_trade_count BIGINT NOT NULL DEFAULT 0,
    average_trade_size NUMERIC(24,6),
    execution_strength_last NUMERIC(18,6),
    execution_strength_average NUMERIC(18,6),
    program_buy_notional NUMERIC(24,2),
    program_sell_notional NUMERIC(24,2),
    program_net_notional NUMERIC(24,2),
    total_bid_quantity BIGINT,
    total_ask_quantity BIGINT,
    bid_quantity_change BIGINT,
    ask_quantity_change BIGINT,
    reconnect_flag BOOLEAN NOT NULL DEFAULT FALSE,
    source_gap_flag BOOLEAN NOT NULL DEFAULT FALSE,
    coverage_ratio NUMERIC(8,6) NOT NULL,
    is_complete BOOLEAN NOT NULL,
    calculated_at TIMESTAMP(6) NOT NULL,
    PRIMARY KEY (bucket_start, interval_seconds, stock_code, trading_venue)
);
SELECT create_hypertable('flow_bar','bucket_start',if_not_exists=>TRUE);
CREATE INDEX IF NOT EXISTS idx_flow_bar_code_interval_time
    ON flow_bar(stock_code, interval_seconds, bucket_start DESC);

COMMENT ON TABLE raw_flow_execution IS 'KIS H0STCNT0 individual-event L0 RAW; timestamps are KST';
COMMENT ON TABLE raw_flow_program IS 'KIS H0STPGM0 individual-event L0 RAW; source quantities/amounts preserved without delta conversion';
COMMENT ON TABLE raw_flow_orderbook_5s IS 'KIS H0STASP0 latest state per completed 5-second KST bucket';
COMMENT ON TABLE flow_bar IS 'Rebuildable L1 FLOW bars derived only from non-duplicate L0 RAW';
COMMENT ON COLUMN flow_bar.coverage_ratio IS '5s: 1 when a connected-window snapshot exists, otherwise 0; 60s: complete 5s child buckets / 12';

CREATE OR REPLACE FUNCTION rebuild_flow_bars(p_start TIMESTAMP, p_end TIMESTAMP)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
  WITH book AS (
    SELECT bucket_start, stock_code, trading_venue, total_bid_quantity, total_ask_quantity,
           total_bid_quantity_change, total_ask_quantity_change,
           reconnect_flag, source_gap_flag OR event_time_regression_flag AS source_gap_flag
    FROM raw_flow_orderbook_5s
    WHERE bucket_start >= date_bin('5 seconds', p_start, TIMESTAMP '2000-01-01')
      AND bucket_start < date_bin('5 seconds', p_end, TIMESTAMP '2000-01-01')
  ), execution AS (
    SELECT date_bin('5 seconds', source_event_time, TIMESTAMP '2000-01-01') AS bucket_start,
           stock_code, trading_venue, count(*) FILTER (WHERE NOT duplicate_flag) AS event_count,
           COALESCE(sum(current_price * abs(execution_volume)) FILTER
             (WHERE NOT duplicate_flag AND execution_classification='1'),0) AS buy_notional,
           COALESCE(sum(current_price * abs(execution_volume)) FILTER
             (WHERE NOT duplicate_flag AND execution_classification='5'),0) AS sell_notional,
           count(*) FILTER (WHERE NOT duplicate_flag AND execution_classification='1') AS buy_count,
           count(*) FILTER (WHERE NOT duplicate_flag AND execution_classification='5') AS sell_count,
           avg(abs(execution_volume)) FILTER (WHERE NOT duplicate_flag) AS avg_trade_size,
           (array_agg(execution_strength ORDER BY source_event_time DESC, received_at DESC)
             FILTER (WHERE NOT duplicate_flag AND execution_strength IS NOT NULL))[1] AS strength_last,
           avg(execution_strength) FILTER (WHERE NOT duplicate_flag) AS strength_avg,
           bool_or(reconnect_flag) AS reconnect_flag,
           bool_or(source_gap_flag OR event_time_regression_flag) AS source_gap_flag
    FROM raw_flow_execution
    WHERE source_event_time >= p_start AND source_event_time < p_end
    GROUP BY 1,2,3
  ), program AS (
    SELECT date_bin('5 seconds', source_event_time, TIMESTAMP '2000-01-01') AS bucket_start,
           stock_code, trading_venue,
           GREATEST(max(buy_execution_amount)-min(buy_execution_amount),0) AS buy_notional,
           GREATEST(max(sell_execution_amount)-min(sell_execution_amount),0) AS sell_notional,
           GREATEST(max(net_buy_execution_amount)-min(net_buy_execution_amount),0) AS net_notional,
           bool_or(reconnect_flag) AS reconnect_flag,
           bool_or(source_gap_flag OR event_time_regression_flag) AS source_gap_flag
    FROM raw_flow_program
    WHERE source_event_time >= p_start AND source_event_time < p_end AND NOT duplicate_flag
    GROUP BY 1,2,3
  )
  INSERT INTO flow_bar (
    bucket_start,bucket_end,interval_seconds,stock_code,trading_venue,event_count,
    aggressive_buy_notional,aggressive_sell_notional,net_aggressive_notional,
    buy_trade_count,sell_trade_count,average_trade_size,execution_strength_last,
    execution_strength_average,program_buy_notional,program_sell_notional,
    program_net_notional,total_bid_quantity,total_ask_quantity,bid_quantity_change,
    ask_quantity_change,reconnect_flag,source_gap_flag,coverage_ratio,is_complete,calculated_at)
  SELECT b.bucket_start,b.bucket_start+INTERVAL '5 seconds',5,b.stock_code,b.trading_venue,
         COALESCE(e.event_count,0),COALESCE(e.buy_notional,0),COALESCE(e.sell_notional,0),
         COALESCE(e.buy_notional,0)-COALESCE(e.sell_notional,0),COALESCE(e.buy_count,0),
         COALESCE(e.sell_count,0),e.avg_trade_size,e.strength_last,e.strength_avg,
         p.buy_notional,p.sell_notional,p.net_notional,b.total_bid_quantity,b.total_ask_quantity,
         b.total_bid_quantity_change,b.total_ask_quantity_change,
         b.reconnect_flag OR COALESCE(e.reconnect_flag,FALSE) OR COALESCE(p.reconnect_flag,FALSE),
         b.source_gap_flag OR COALESCE(e.source_gap_flag,FALSE) OR COALESCE(p.source_gap_flag,FALSE),
         1.0,
         NOT (b.source_gap_flag OR b.reconnect_flag OR COALESCE(e.source_gap_flag,FALSE)
              OR COALESCE(e.reconnect_flag,FALSE) OR COALESCE(p.source_gap_flag,FALSE)
              OR COALESCE(p.reconnect_flag,FALSE)),CURRENT_TIMESTAMP
  FROM book b
  LEFT JOIN execution e USING (bucket_start,stock_code,trading_venue)
  LEFT JOIN program p USING (bucket_start,stock_code,trading_venue)
  ON CONFLICT (bucket_start,interval_seconds,stock_code,trading_venue) DO UPDATE SET
    bucket_end=EXCLUDED.bucket_end,event_count=EXCLUDED.event_count,
    aggressive_buy_notional=EXCLUDED.aggressive_buy_notional,
    aggressive_sell_notional=EXCLUDED.aggressive_sell_notional,
    net_aggressive_notional=EXCLUDED.net_aggressive_notional,buy_trade_count=EXCLUDED.buy_trade_count,
    sell_trade_count=EXCLUDED.sell_trade_count,average_trade_size=EXCLUDED.average_trade_size,
    execution_strength_last=EXCLUDED.execution_strength_last,
    execution_strength_average=EXCLUDED.execution_strength_average,
    program_buy_notional=EXCLUDED.program_buy_notional,program_sell_notional=EXCLUDED.program_sell_notional,
    program_net_notional=EXCLUDED.program_net_notional,total_bid_quantity=EXCLUDED.total_bid_quantity,
    total_ask_quantity=EXCLUDED.total_ask_quantity,bid_quantity_change=EXCLUDED.bid_quantity_change,
    ask_quantity_change=EXCLUDED.ask_quantity_change,reconnect_flag=EXCLUDED.reconnect_flag,
    source_gap_flag=EXCLUDED.source_gap_flag,coverage_ratio=EXCLUDED.coverage_ratio,
    is_complete=EXCLUDED.is_complete,calculated_at=EXCLUDED.calculated_at;

  WITH minute AS (
    SELECT date_bin('1 minute',bucket_start,TIMESTAMP '2000-01-01') AS minute_start,
           stock_code,trading_venue,count(*) AS child_count,
           sum(event_count) AS event_count,sum(aggressive_buy_notional) AS buy_notional,
           sum(aggressive_sell_notional) AS sell_notional,sum(buy_trade_count) AS buy_count,
           sum(sell_trade_count) AS sell_count,
           sum(average_trade_size*event_count)/NULLIF(sum(event_count),0) AS avg_trade_size,
           (array_agg(execution_strength_last ORDER BY bucket_start DESC)
             FILTER (WHERE execution_strength_last IS NOT NULL))[1] AS strength_last,
           avg(execution_strength_average) AS strength_avg,sum(program_buy_notional) AS program_buy,
           sum(program_sell_notional) AS program_sell,sum(program_net_notional) AS program_net,
           (array_agg(total_bid_quantity ORDER BY bucket_start DESC))[1] AS total_bid,
           (array_agg(total_ask_quantity ORDER BY bucket_start DESC))[1] AS total_ask,
           sum(bid_quantity_change) AS bid_change,sum(ask_quantity_change) AS ask_change,
           bool_or(reconnect_flag) AS reconnect_flag,bool_or(source_gap_flag) AS source_gap_flag,
           count(*) FILTER (WHERE is_complete) AS complete_count
    FROM flow_bar WHERE interval_seconds=5 AND bucket_start >= date_bin('1 minute',p_start,TIMESTAMP '2000-01-01')
      AND bucket_start < date_bin('1 minute',p_end,TIMESTAMP '2000-01-01')
    GROUP BY 1,2,3
  )
  INSERT INTO flow_bar (
    bucket_start,bucket_end,interval_seconds,stock_code,trading_venue,event_count,
    aggressive_buy_notional,aggressive_sell_notional,net_aggressive_notional,buy_trade_count,
    sell_trade_count,average_trade_size,execution_strength_last,execution_strength_average,
    program_buy_notional,program_sell_notional,program_net_notional,total_bid_quantity,total_ask_quantity,
    bid_quantity_change,ask_quantity_change,reconnect_flag,source_gap_flag,coverage_ratio,is_complete,calculated_at)
  SELECT minute_start,minute_start+INTERVAL '1 minute',60,stock_code,trading_venue,event_count,
         buy_notional,sell_notional,buy_notional-sell_notional,buy_count,sell_count,avg_trade_size,
         strength_last,strength_avg,program_buy,program_sell,program_net,total_bid,total_ask,bid_change,
         ask_change,reconnect_flag,source_gap_flag,complete_count/12.0,
         child_count=12 AND complete_count=12 AND NOT reconnect_flag AND NOT source_gap_flag,CURRENT_TIMESTAMP
  FROM minute
  ON CONFLICT (bucket_start,interval_seconds,stock_code,trading_venue) DO UPDATE SET
    bucket_end=EXCLUDED.bucket_end,event_count=EXCLUDED.event_count,
    aggressive_buy_notional=EXCLUDED.aggressive_buy_notional,
    aggressive_sell_notional=EXCLUDED.aggressive_sell_notional,
    net_aggressive_notional=EXCLUDED.net_aggressive_notional,buy_trade_count=EXCLUDED.buy_trade_count,
    sell_trade_count=EXCLUDED.sell_trade_count,average_trade_size=EXCLUDED.average_trade_size,
    execution_strength_last=EXCLUDED.execution_strength_last,
    execution_strength_average=EXCLUDED.execution_strength_average,
    program_buy_notional=EXCLUDED.program_buy_notional,program_sell_notional=EXCLUDED.program_sell_notional,
    program_net_notional=EXCLUDED.program_net_notional,total_bid_quantity=EXCLUDED.total_bid_quantity,
    total_ask_quantity=EXCLUDED.total_ask_quantity,bid_quantity_change=EXCLUDED.bid_quantity_change,
    ask_quantity_change=EXCLUDED.ask_quantity_change,reconnect_flag=EXCLUDED.reconnect_flag,
    source_gap_flag=EXCLUDED.source_gap_flag,coverage_ratio=EXCLUDED.coverage_ratio,
    is_complete=EXCLUDED.is_complete,calculated_at=EXCLUDED.calculated_at;
END $$;

COMMIT;
