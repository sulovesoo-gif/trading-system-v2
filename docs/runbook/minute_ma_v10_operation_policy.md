# Minute MA V1.0 operation-policy runbook

## Frozen contract

- Legacy Minute MA remains 2,400 strategies × 8 axes = 19,200 paths.
- V1.0 adds 2,400 `KRX_CONTINUOUS` policy identities; it does not reinterpret a legacy path.
- SHORT PAPER/LIVE entry windows are 09:00–09:59 / 09:00–09:29; stop is underlying +1%.
- LONG PAPER/LIVE entry windows are 14:00–15:18 / 15:00–15:18; stop is underlying -5%.
- Both directions hold until normal MA exit or their own trade-specific stop. There is no V1 EOD exit.

The STOP proxy is copied from
`Trading_System_V2_MinuteMA_본주손절_1to5pct_전수검증_V0.1_20260827.sql`:

1. anchor = KRX underlying OPEN at that trade's PAPER entry execution minute;
2. trigger = first completed underlying 1MIN CLOSE at/after entry that crosses the adverse threshold;
3. PAPER exit = first actual execution-product KRX 1MIN OPEN strictly after the trigger;
4. LIVE exit = submit immediately after completed-bar confirmation, with broker fill authoritative.

For LIVE ENTRY, the bridge obtains the exact KRX underlying `stck_oprc` for the
PAPER entry minute from `FHKST03010200`; a missing or ambiguous exact-minute
row fails closed. It never substitutes the current quote. The execution-product
reference price remains separate and broker fill remains the actual execution
source.

## TEST-only order

1. Apply `20260827_minute_ma_actual_send_additive.sql` if its tables are absent.
2. Apply `20260828_minute_ma_v10_policy_additive.sql`.
3. Run `20260828_minute_ma_v10_policy_verify.sql`.
4. Run `scripts/db/run_minute_ma_v1_prod_readiness_fixture.py`; it applies the
   prepared plan and exercises production repositories inside one transaction,
   then always rolls back.
5. Run `20260828_minute_ma_v10_policy_test_e2e.sql`; it rolls back all fixture rows.
6. Run Minute and Daily focused regression suites.

## HOLD boundaries

This implementation does not authorize operating DB apply, Selection approval,
Operation/Capital Epoch creation, systemd enablement, or Actual SEND.  The
candidate plan is evidence-only (`approval_status='HOLD'`).  Both the DB send
profile and `MINUTE_MA_ACTUAL_SEND` must remain `N`.

The two pre-existing test LIVE paths (DS001283 INTEGRATED_CONTINUOUS and
DS002277 KRX_CONTINUOUS) remain history-preserved and outside the V1 candidate
plan.

## Future approved operation apply

After a separate user approval, run the guarded prepared apply migration. It
creates a scoped V1 Selection batch/snapshot, independent policy Operations and
Capital Epochs, and returns the two legacy test paths to PAPER while preserving
their history. Verify 20 V1 LIVE paths and total initial capital 3,200,000.
Never reinterpret a legacy minute operation as a V1 operation.
