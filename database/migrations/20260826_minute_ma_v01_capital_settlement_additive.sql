BEGIN;
CREATE TABLE IF NOT EXISTS minute_ma_live_capital_settlement (
  settlement_id UUID PRIMARY KEY,
  minute_live_trade_id BIGINT NOT NULL UNIQUE REFERENCES minute_ma_live_trade(minute_live_trade_id),
  minute_path_id BIGINT NOT NULL REFERENCES minute_ma_path(minute_path_id),
  capital_epoch_no INTEGER NOT NULL,
  entry_filled_amount NUMERIC NOT NULL,
  exit_filled_amount NUMERIC NOT NULL,
  gross_realized_pnl NUMERIC NOT NULL,
  buy_fee NUMERIC NOT NULL DEFAULT 0,
  sell_fee NUMERIC NOT NULL DEFAULT 0,
  sell_tax NUMERIC NOT NULL DEFAULT 0,
  other_cost NUMERIC NOT NULL DEFAULT 0,
  net_realized_pnl NUMERIC NOT NULL,
  settled_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
COMMIT;
