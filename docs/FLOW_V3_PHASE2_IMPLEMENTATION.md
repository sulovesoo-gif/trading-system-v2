# FLOW V3 phase 2 PAPER accounting

This release implements PAPER accounting and a bounded read-only FLOW dashboard.
It does not activate LIVE SEND, create LIVE operations/capital, or add a broker
adapter. The 15 candidate IDs also have an explicit NO_SEND preparation ledger;
neither the display filter nor that ledger grants broker authorization.

## Provenance and preserved contracts

Cost basis: supplied `07_Daily_Incremental_PAPER_Update_V0.1` section J.
Gross percent = (exit / entry - 1) * 100; net percent deducts 0.0693054
percentage points. Both fees/slippage are ENTRY-notional research deductions;
they are not broker-calculated fees. No tax term is invented.

Initial independent PAPER capital = prior completed KRX daily close * 1.5.
The required date is the latest KRX DAILY date before the strategy's first
actual PAPER entry. A missing product quote on that date blocks that strategy's
projection with `MISSING_PREVIOUS_KRX_CLOSE`, without affecting PAPER tracking.
Shares = max(0, floor(confirmed capital / entry execution price)). Every lot
retains its own shares. Existing OPENs do not block later entries or reserve
cash. At equal timestamps: older-lot EXITs, then all new ENTRY sizings, then
zero-duration lots' own EXITs. Paper trade ID is the stable in-phase tie-breaker.
Only CLOSED lot net realized P&L updates capital. No mark-to-market capital.

Existing research percentage summaries are preserved. New independent-share
capital, daily metrics, and lot accounting use additive PAPER-only projections.
Daily settlement belongs to actual exit date; entry counts belong to entry date.
Dates with no events have no materialized daily row. Cumulative fields cover
all available history; dashboard selected-period fields are separate.

## Recovery and execution

`run_flow_v3_paper_accounting.py` uses an independent service/pool. A source
INSERT/relevant UPDATE enqueues the strategy in the same source transaction.
The worker uses an advisory singleton lock and repeatable-read transactions,
rebuilds one strategy in time order, and atomically upserts absolute projections.
Queue generation is compared on acknowledgement. Concurrent updates cause
serialization retry rather than lost queue work. Failed strategies are deferred
60 seconds with a durable error; other strategies continue. Source prices,
trade lifecycle, RAW, and existing broker services are never edited.

Start/stop/restart: `systemctl start|stop|restart trading-flow-v3-paper-accounting.service`.
Manual bounded cycle: `venv/bin/python scripts/runtime/run_flow_v3_paper_accounting.py --once`.
Dashboard: `/flow-v3.html`; API `/flow-v3/api/dashboard`.
Reuse: Minute period/page helpers, 20/50 page limits, sticky identity columns,
LIVE/PAPER paired table rows, safe escaped rendering, on-demand filters.

## Minimal read-only checks

```sql
SELECT count(*) FROM flow_v3_strategy_master WHERE is_enabled='Y';
SELECT strategy_id,last_error FROM flow_v3_paper_accounting_queue
WHERE last_error IS NOT NULL ORDER BY strategy_id;
SELECT strategy_id,initial_price_date,initial_price,metrics
FROM flow_v3_paper_accounting_capital
WHERE strategy_id IN ('FV3008243','FV3008241','FV3005688');
SELECT strategy_id,business_date,metrics FROM flow_v3_paper_accounting_daily
WHERE strategy_id='FV3008243' ORDER BY business_date;
```

## Remaining scope

The preparation migration and `prepare_flow_v3_live_candidates.py` initialize
15 independent snapshots from the last completed KRX daily close * 1.5.
`next_quantity` is indicative at that prior close, not an executable quote.
The schema enforces SEND false and a non-submittable status. New post-cutover
PAPER signal identities are linked to a separate preparation-intent table.
Historical signals cannot create these intents. Late PAPER trade links are
completed idempotently. No shared live_order_request/submit-claim rows are made.
Verified broker cost-finalized settlements must be integrated before this
initial snapshot can become a real live compound-capital ledger.

LIVE activation and broker lifecycle integration are not delivered by this
PAPER release. Existing MFE/MAE are not recalculated. Shared 80-million-won
account simulation is not implemented or populated with fictional numbers.
Natural market-input recovery remains the separate collector/runtime concern;
this service neither reconstructs missing RAW nor manufactures signals.
