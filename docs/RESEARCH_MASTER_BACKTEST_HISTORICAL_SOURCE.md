# Research Master Historical Source

`run_strategy_master_backtest(date,date,numeric)` is the historical source for
the 802 Research master rows.  The procedure is read from PostgreSQL with
`scripts/runtime/export_strategy_master_backtest_definition.py`; it is not
reconstructed from the FROZEN LIVE Champion implementation.

Captured operating definition:

```text
bytes   = 40391
sha256  = 12bd4131aa9fddd8537b90326293720617b61dface77d871d4ad6b2635dae21d
```

The procedure creates the common completed-1MIN base/features, then dispatches
these historical groups:

- `S1_OR_PULLBACK_RESTART`
- `S2_FAILED_OR_VWAP`
- `S3_VOLUME_CLIMAX_REVERSAL`
- `S4_AFTERNOON_MOMENTUM`

The master adapter joins signals on `strategy_group`, `signal_stock_code`,
`signal_direction`, and `entry_variant`.  It supplies the common exit engine
from `exit_params` and the execution product mapping from the master row.

`src/research_core/engine.py` deliberately reproduces those semantics.  It is
not the FROZEN LIVE Core and must not be used to reinterpret the four LIVE
Champion definitions.

Historical caveat preserved by the Core: for the SQL type
`STRUCTURE_MAX30_STOP`, the procedure selects the structure/max-30 path but
does not consume `stop_pct`.  Research Core retains that behavior until a
separate versioned Research semantics change is approved.

Exact replay is run through
`scripts/runtime/exact_replay_research_master_rollback.py`.  The procedure
oracle is invoked only inside a transaction that is rolled back, so the test
does not persist research runs, candidates, approvals, orders, fills, or RAW
changes.
