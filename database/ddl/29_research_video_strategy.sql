/* VIDEO_STRATEGY V1 research-only additive schema.  Never changes RAW/live rows. */
SET TIME ZONE 'Asia/Seoul';

ALTER TABLE research_feature ADD COLUMN IF NOT EXISTS strategy_family VARCHAR(40);
ALTER TABLE research_feature ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(20);
ALTER TABLE research_feature ADD COLUMN IF NOT EXISTS feature_detail JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE research_signal_event ADD COLUMN IF NOT EXISTS strategy_family VARCHAR(40);
ALTER TABLE research_signal_event ADD COLUMN IF NOT EXISTS strategy_version VARCHAR(20);
ALTER TABLE research_signal_event ADD COLUMN IF NOT EXISTS event_detail JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS research_video_event_performance (
    event_id BIGINT NOT NULL REFERENCES research_signal_event(event_id) ON DELETE CASCADE,
    execution_stock_code VARCHAR(20) NOT NULL,
    signal_direction VARCHAR(10) NOT NULL CHECK (signal_direction IN ('LONG','SHORT')),
    execution_direction VARCHAR(20) NOT NULL CHECK (execution_direction IN ('LONG','SHORT','VIRTUAL_SHORT')),
    event_price NUMERIC(18,2),
    trade_price NUMERIC(18,2),
    return_1m NUMERIC(18,10), return_3m NUMERIC(18,10), return_5m NUMERIC(18,10),
    return_10m NUMERIC(18,10), return_20m NUMERIC(18,10), return_30m NUMERIC(18,10),
    mfe NUMERIC(18,10), mae NUMERIC(18,10),
    data_status VARCHAR(30) NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id, execution_stock_code)
);

ALTER TABLE research_video_event_performance
    ALTER COLUMN execution_direction TYPE VARCHAR(20);

CREATE INDEX IF NOT EXISTS ix_research_video_event_performance_stock
    ON research_video_event_performance(execution_stock_code,event_id);

COMMENT ON TABLE research_video_event_performance IS
  'VIDEO_STRATEGY source event의 exact-timestamp execution 상품별 forward return 및 HIGH/LOW MFE/MAE.';
