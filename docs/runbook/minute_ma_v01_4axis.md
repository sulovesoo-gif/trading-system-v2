# Minute MA V0.1 base + AFTERNOON runbook

This layer is additive. It does not alter Research 802, Forward, Daily MA, RAW,
or the shared broker ledgers. `MINUTE_MA_LIVE_SEND` is created disabled.

## Prerequisites

- Daily canonical enabled universe is exactly 2,400.
- Shared DDLs for `live_order_request`, broker fill allocation, logical
  ownership and reconciliation have passed.
- RAW KRX minute bars are read only in 09:00–15:30 and INTEGRATED only in
  08:00–19:59.

## Apply and verify

Apply `database/migrations/20260826_minute_ma_v01_additive.sql` to TEST first,
then execute `database/migrations/20260826_minute_ma_v01_verify.sql`. Expected
seed counts are 2,400 semantic strategies, 2,400 paths per axis, 9,600 current
PAPER operations, and 9,600 paper-capital rows. Send profile must be `N`.

Apply `database/migrations/20260826_minute_ma_afternoon_additive.sql` only
after the base migration. It adds four path variants without duplicating the
2,400 semantic strategies. The resulting registry is eight axes and 19,200
paths/current operations/paper-capital rows. AFTERNOON keeps the base axis MA
history and changes only ENTRY source-time eligibility: CONTINUOUS 14:00–15:18,
RESET 14:00–14:59. Actual SEND remains locked.

## PAPER

NO_WRITE is the default:

```text
python scripts/runtime/run_minute_ma_paper.py --date YYYY-MM-DD
```

TEST PAPER writes require both `--write` and
`MINUTE_MA_PAPER_WRITE=Y`. Re-running the same date is idempotent by the
deterministic signal-event and trade keys.

## Initial historical selection

The seed command requires all three approved artifacts and validates exact
2,400-row identity equality before opening a database transaction:

```text
python scripts/db/seed_minute_ma_selection_20260826.py \
  --krx-continuous EXCEL2_01_KRX_CONTINUOUS_202608.csv \
  --krx-reset EXCEL2_02_KRX_RESET_202608.csv \
  --integrated-continuous EXCEL2_03_INTEGRATED_CONTINUOUS_202608.csv \
  --evaluation-from YYYY-MM-DD --evaluation-to YYYY-MM-DD --approve
```

Only axis rows with compound return at least 10% receive SELECTED and the
20,000 KRW approved start amount. INTEGRATED_RESET remains PENDING. Operation
and capital are not changed unless the separately explicit
`--apply-live-operation` flag is provided.

## Dashboard

The existing read-only dashboard service exposes `/minute-ma`; API endpoints
are `/minute-ma/api/dashboard` and `/minute-ma/api/detail`. The grid does not
lock or mutate trading state.

## LIVE NO_SEND

`PostgresMinuteMaNoSendAdapter` prepares an idempotent shared
`live_order_request` only for current LIVE paths after reconciliation and cash
checks. It has no broker submit method. Insufficient-cash events are durable
and never retried. Actual SEND stays locked pending a separate user approval
and a future minute-runtime SEND audit.

## Rollback

The guarded rollback refuses to run once approved selection or any PAPER/LIVE
history exists. It never deletes Daily MA, Research 802, Forward, RAW, or
shared broker rows.
