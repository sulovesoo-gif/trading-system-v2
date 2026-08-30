BEGIN;

-- The original overlapping-compound research contract does not floor virtual
-- capital at zero.  This research-only column must preserve those source
-- results exactly; it is never an Operation/Capital/cash-gate value.
ALTER TABLE minute_ma_policy_historical_trade
  DROP CONSTRAINT IF EXISTS minute_ma_policy_historical_trade_basis_capital_check;

COMMIT;
