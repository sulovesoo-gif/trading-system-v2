BEGIN;
CREATE TABLE IF NOT EXISTS daily_strategy_live_risk_state (
 strategy_id VARCHAR(20) PRIMARY KEY REFERENCES daily_strategy_master(strategy_id),
 live_risk_status VARCHAR(32) NOT NULL CHECK(live_risk_status IN ('ENABLED','THREE_STRIKE_SUSPENDED')) DEFAULT 'ENABLED',
 consecutive_loss_streak INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_loss_streak>=0),
 last_risk_event_at TIMESTAMP NULL,updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS daily_strategy_live_risk_event (
 risk_event_id UUID PRIMARY KEY,paper_trade_id BIGINT NOT NULL UNIQUE REFERENCES daily_strategy_paper_trade(paper_trade_id),
 strategy_id VARCHAR(20) NOT NULL REFERENCES daily_strategy_master(strategy_id),return_pct NUMERIC NOT NULL,
 prior_streak INTEGER NOT NULL CHECK(prior_streak>=0),resulting_streak INTEGER NOT NULL CHECK(resulting_streak>=0),
 resulting_status VARCHAR(32) NOT NULL CHECK(resulting_status IN ('ENABLED','THREE_STRIKE_SUSPENDED')),
 reason VARCHAR(48) NOT NULL,processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO daily_strategy_live_risk_state(strategy_id)
SELECT o.strategy_id FROM daily_strategy_operation o JOIN daily_strategy_master m USING(strategy_id)
WHERE o.effective_to IS NULL AND o.operation_status='LIVE' AND m.strategy_role='CANONICAL' AND m.is_enabled='Y'
ON CONFLICT(strategy_id) DO NOTHING;
DO $$ DECLARE n bigint; BEGIN SELECT count(*) INTO n FROM daily_strategy_live_risk_state; IF n<>346 THEN RAISE EXCEPTION 'expected 346 initial risk states, got %',n; END IF; END $$;
COMMIT;
