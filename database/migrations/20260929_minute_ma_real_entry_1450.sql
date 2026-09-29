BEGIN;

-- V1.5 changes only the REAL PAPER/LIVE shared ENTRY window start.
-- Existing trades, OPEN lifecycles, routes and capital epochs are untouched.
ALTER TABLE minute_ma_real_paper_trade
  DROP CONSTRAINT IF EXISTS minute_ma_real_paper_trade_entry_signal_time_check;
ALTER TABLE minute_ma_real_paper_trade
  ADD CONSTRAINT minute_ma_real_paper_trade_entry_signal_time_check
  CHECK (
    entry_signal_time::time >= TIME '14:50'
    AND entry_signal_time::time < TIME '15:19'
  );

COMMIT;
