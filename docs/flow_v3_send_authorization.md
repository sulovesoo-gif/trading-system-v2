# FLOW dual approval

Only `FLOW_V3_ACTUAL_SEND=Y` AND the independent `flow_v3_send_profile`
`FLOW_V3_LIVE_SEND` row set to `Y` authorize the existing FLOW transport.
Missing/malformed/mismatched values deny SEND. Migration and installed unit default N.
Minute/Daily profiles and services are not reused or changed.

The historical unit name `trading-flow-v3-live-nosend.service` and READY_NO_SEND
ledger labels are retained for compatibility; they are not approval flags.
`flow_v3_live_preparation` remains an immutable no-send preparation snapshot.
Actual order/worker ledgers no longer have a permanent false constraint.
Actual order/cancel attempt counters are restricted to 0..1; unique event/lot keys
and claimed/unknown states still prevent technical resend.

Orders prepared while disabled remain disabled. Intents created before the DB
approval timestamp cannot become armed later. No bulk arming or historical replay.
Transport validates the exact whitelist/product/side/quantity/payload, commits the
claim, then reads current ENV+DB approval again immediately before HTTP. An approval
revoked after claim yields a durable pre-POST denial with no HTTP call; an ambiguous
transport failure remains UNKNOWN, never automatically retried.

## Human activation (not run by deployment)

This command enables real automatic orders on subsequent natural signals. It does
not emit a synthetic signal or test order. Run only after reviewing the deployment.

```powershell
ssh trading-v2 'cd /home/ubuntu/projects/trading-system-v2 && venv/bin/python scripts/ops/flow_v3_send_approval.py --enable'
```

The helper checks the existing 15 LIVE operations, exact mapping and capital
invariants, sets only the FLOW DB profile and FLOW unit drop-in, and restarts only
the existing FLOW LIVE worker. Configuration failures revert FLOW approval to N.
Use `--check` instead for a read-only preflight. No other service is restarted.
