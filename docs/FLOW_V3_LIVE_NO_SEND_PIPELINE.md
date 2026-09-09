# FLOW V3 LIVE pipeline — physical No-SEND release

PAPER correction commit 49612795 and all original PAPER/RAW remain unchanged.
Only the approved 15 strategies can enter the independent LIVE ledger. Operation
promotion does not disable PAPER. `strategy_id + entry_event_key` identifies an
ENTRY. Existing OPEN lots never block another ENTRY. Each lot has its own EXIT
lifecycle, distinct from strategy ownership.

## Storage and running

- Migration: `20260909_flow_v3_live_pipeline.sql`.
- `flow_v3_live_capital`: previous completed KRX close × 1.5, realized-only balance.
- `flow_v3_live_intent`, `flow_v3_live_order`: immutable event identity, request body,
  explicit REJECTED vs UNKNOWN, durable broker-number association.
- `flow_v3_live_fill_checkpoint`: exact broker cumulative deltas, observation time
  separate from optional actual fill time. A daily order timestamp is NOT a fill timestamp.
- Existing `flow_v3_live_trade` and new `flow_v3_live_lot`: actual broker fill prices,
  independent exposure and original PAPER event link.
- `flow_v3_live_settlement`: exactly-once actual-cost-finalized NET into capital.
- `flow_v3_live_checkpoint_allocation`: FLOW broker-day attribution added; the shared
  cost reader changes only its FLOW date selection. Daily/Minute branches unchanged.
- `trading-flow-v3-live-nosend.service`: independent worker, no shared order queue.
  Start/stop/restart with systemctl; initial activation is the explicit separate
  `scripts/runtime/run_flow_v3_live.py --activate-approved --once` command.

The worker processes existing broker observations and finalized settlements before
new ENTRY sizing. Quantity is floor(current realized capital / current KRX quote).
Quotes older than 30 seconds do not size an order. Historical events before
activation are not replayed; stale entry observations are durably blocked individually.

## Execution and recovery boundaries

Request objects contain the existing KRX market-order endpoint/TR/body, without
account secrets. `record_response` handles an authoritative submit response;
`observe` handles exact order-number matched cumulative history. Unknown orders
without a durable number are never matched by quantity/time and never resent.
`live_transport.py` connects request/cancel claims to the response hooks but the
installed code gate and DB gates are closed; this release DOES NOT call order POST.
Both the Python boundary and database
constraints physically prohibit SEND. No existing authorization is changed.

An unfilled or partially filled entry remains exposed; an exit waits for the entry
order to be terminal, preventing a later buy fill from being omitted. An exit
closes only actual filled quantity. Missing cost information retains
`FILLED_COST_PENDING`, not fabricated zero fees. Broker allocation must cover both
BUY and SELL notional exactly. Price slippage is already in actual fill PNL and is
not deducted twice as a hypothetical PAPER cost.

Normal EXIT uses each lot's frozen family/exit pair. EOD waits for source processing
through 15:18, then creates its 15:19 market request. No paper price is copied into
LIVE. Missing/late final source or unresolved orders never becomes a fictitious
CLOSED trade. HOLD lots survive trade-date boundaries. New ENTRY cutoff for both
policies is 15:18.

## Explicit remaining operational boundary

No natural FLOW broker ACK/fill can be demonstrated while SEND is disabled.
Shared actual-cost finalization is consumed, never reallocated independently by
FLOW. The current existing shared finalization schedule supplies final snapshots.
At EXIT, `flow_v3_live_entry_release` tracks the original entry order independently:
broker cancellable-quantity inquiry → cancel request object → cancellation response
→ fresh terminal history → final filled quantity → sell only the lot's remainder.
Cancellation ACK alone never unlocks SELL. The 5→3 and cancellation-race 5→4 cases
are verified against temporary PostgreSQL fixtures. A zero-fill cancellation creates
neither a synthetic LIVE lot nor a zero-quantity SELL. Cancel POST is also physically
disabled in this release; callbacks are verified without real broker transactions.

Official cancellation reference:
https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/order_rvsecncl/order_rvsecncl.py
Official cancellable inquiry (TTTC0084R, paginated):
https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_psbl_rvsecncl/inquire_psbl_rvsecncl.py

`DASHBOARD_VISUAL_VERIFICATION_PENDING` is nonblocking; DB/API checks are separate.
