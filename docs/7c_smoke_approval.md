# 7C Smoke approval gate

This document defines controls only.  No 7C-1 BUY is approved by this commit.
Each phase (7C-1 BUY, 7C-2 SELL, 7C-3 recovery, 7C-4 cycle) requires a new
explicit approval. Passing one phase never enables the next.

## Product whitelist

| Code | Name | Type | BUY | SELL |
|---|---|---|---|---|
| 0193W0 | KODEX Samsung Electronics single-stock leverage | ETF | candidate | candidate |
| 0193L0 | PLUS Samsung Electronics single-stock inverse 2X | ETF | candidate | candidate |
| 0197X0 | SOL SK hynix single-stock inverse 2X | ETF | candidate | candidate |

`active_product`, `active_strategy_instance`, and `allowed_time_window` default
to null. Therefore this document/configuration cannot submit any order. A later
stage approval must set exactly one product and one strategy instance.

## Non-negotiable contracts

- Quantity is exactly `1`; any other value blocks before submit.
- 7C-1 permits BUY only; 7C-2 SELL needs durable actual position quantity 1.
- Daily real submit limit is 1 and outstanding broker order limit is 1.
- UNKNOWN never resends; recovery may query broker only.
- Kill switch is durable and defaults to BLOCKED. Any mismatch flips it to
  BLOCKED until an operator explicitly re-approves a new phase.
- Every transition requires audit attributes: timestamp, strategy instance,
  stock, side, quantity, client/broker/trade ids, before/after state.
- Strategy attribution remains separate even for the same physical product.

## 7C-1 test plan (not executed)

1. Operator enables exactly one phase-1 product/instance/window and kill switch.
2. Validate BUY/1/share/day/outstanding/whitelist/time/position contracts.
3. Submit once only after a separate approval.
4. Reconcile ACK, broker order, fills, durable state, and attributed position.
5. On any mismatch: flip durable kill switch BLOCKED; perform no resend or
   additional order. SELL is reserved for separately approved 7C-2.
