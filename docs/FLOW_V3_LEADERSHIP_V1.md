# FLOW V3 Leadership V1 — local implementation / deployment not performed

## Isolation boundary

All application, batch, UI, migration, unit and test files are new. Existing FLOW,
Minute, Daily, Dashboard, PAPER, broker, SEND and collector files are not edited.
Only pure `FlowV3SignalEngine` state mathematics and model classes are imported.
No trading repository or KIS client is instantiated. No simulated trade is inserted
into the original PAPER/LIVE ledgers. No existing source row is updated/deleted.

The reader uses a REPEATABLE READ, READ ONLY transaction. The batch has a separate
writer DSN and refuses a writer that has INSERT/UPDATE/DELETE/TRUNCATE rights on
any existing public source table. Publication INSERTs only the two new Leadership
tables. No shared `.env` fallback exists. Dashboard connections are always READ ONLY.

The future migration includes the explicitly requested **new** common-code group
and its 12 rows. This is the sole exception to the new-table-only migration rule;
it does not update any existing common-code row. No migration ran in production.

## Observed source inventory (2026-09-15 READ ONLY)

- Active master: 9,600; LONG 4,800, SHORT 4,800.
- UNDERLYING eligible: active LONG, stock 000660 or 005930: **4,800**. Each stock
  has 1,200 SIGNAL_EOD + 1,200 SIGNAL_HOLD. No direct short-sale contract is invented.
- Common code PK `(group_cd, code)`, group PK `group_cd`; labels `code_name`,
  `sort_order`, `use_yn`; typed business values are in group-defined attrs. There is
  no group_id/value column. New group attr1 = integer KRW; attr2 = default Y/N.
- `MARKET/INTEGRATED.attr5=20:00`; repo market_session has same end. 16:00 is the
  user's research eligibility start, **not** a FLOW reset boundary.
- On 2026-09-14, extended execution RAW (`raw_flow_execution`, stored KRX,
  H0STCNT0): 000660 22,340 rows; 005930 36,407; both 16:00:00–19:59:59.
- `raw_flow_program`: 000660 1,201 rows, 16:00:04–20:00:06;
  005930 408, 16:00:05–19:59:35 (audit bounded through 20:01).
- `raw_flow_orderbook_5s`: each stock 2,881 rows, 16:00:04–20:00:00.
- Execution classifications 1/5 and positive prices were present. Stored venue
  labels are retained as provenance, not relabelled as NXT/INTEGRATED.
- **No `raw_stock_minute` rows at 16:00–20:01** for these stocks on that date.
  Regular price source: KIS/KRX/1MIN/KOSPI, 778 combined rows that day.
- Stored `flow_v3_minute_state`: 43 minutes per stock on 9/14, 14:38–15:30.
  Therefore extended state is independently rebuilt from actual RAW, not assumed
  to be present in the operating state table.

## Price and signal contracts

REGULAR reads existing PAPER event/lifecycle times and the approved
`flow_v3_paper_contract_correction` overlay. Exclusions and the 15:18 ENTRY cutoff
are retained. Existing PAPER execution prices (ETPs) are **not** used for STOCK:
exact corresponding KIS/KRX/1MIN underlying OPEN is selected. EOD is independently
reverse-selected at or before 15:19, never 15:20+. Conflicting prices for the same
stock/time/source fail closed; identical duplicate source rows are collapsed.

Extended signal mathematics: FLOW fast/slow, Velocity, F1/F2/F3/F4, P0/P1/P2 and
reverse EXIT are unchanged. Same-day 09:00-through-end state continues past 16:00;
gaps invalidate contiguous windows, missing bars are never generated. Date changes
reset state; HOLD lots/exit responsibility survive. Only eligibility changes:

| Policy | ENTRY signals | EXIT signals |
|---|---|---|
| REGULAR | 09:00–15:18 | EOD through 15:18; HOLD preserves original regular EXIT through 15:30 |
| EXTENDED_EXIT | 09:00–15:18 (execution stays regular) | regular through MARKET.KRX end + 16:00–before configured end |
| EXTENDED_FULL | regular + extended | regular + extended |
| AFTER | extended only | extended only, with regular-state warmup |

Normal research execution uses the next **observed eligible** underlying OPEN
after the signal. Extended EOD reverse-searches a same-day observed OPEN at/before
the configured session end (20:00 currently), provided it is not before ENTRY.
This extends the regular backward-search contract; no market quote, forward-fill,
PAPER ETP price or fabricated tick bar substitutes for an absent OPEN.

**Production extended price/EOD usability remains unverified.** In current source
conditions extended metrics are NULL with `EXTENDED_PRICE_UNAVAILABLE`, not 0%.
Missing earlier-day warmup/extended coverage is also explicit and prevents a
false complete HOLD result. All source trading dates since the initial research
date are required; a later start that would discard earlier HOLD is rejected.

## Capital / cost / period accounting

- Common-code axes (KRW): 5m, 6m (default), 10m, 15m, 20m, 30m, 40m, 50m,
  60m, 70m, 80m, 100m. Source code never generates alternative capital axes.
- Continuous independent per-strategy, per-policy, per-capital replay from the
  first source PAPER date. Each ENTRY uses floor(current realized capital / OPEN).
  Multiple independent entries may overlap, as in existing independent PAPER
  accounting. **Not** a shared cash/slot/D+2 capacity simulation; no 30m gate.
- STOCK costs: 09A V0.8 STOCK_LONG `0.000140527` on BUY notional and SELL notional.
  Net = quantity*(exit-entry) - quantity*(entry+exit)*rate. No extra unapproved
  stock tax/slippage is invented; ETP fee constants are not applied to STOCK.
- Only realized NET updates capital. No current-capital cap and no fixed share.
- Timestamp order: earlier lot EXIT, all new ENTRY, same-lot zero-duration EXIT.
- Daily, WTD (Monday), MTD are measured from **realized capital at the corresponding
  boundary**; carried OPEN lots retain original quantities. Weekend/holiday
  boundaries naturally resolve to the first source trading event. `capital_base`
  is the independent initial research axis; period `initial_capital` may differ
  after previous gains/losses. `final_capital` includes realized results through
  the snapshot date. Overnight PnL belongs to the realization date.
- MDD: realized-capital peak-to-trough percentage, not mark-to-market. Zero-share
  entries are counted separately and do not inflate actual trade/win counts.

## Snapshot and rank

PK `(snapshot_date,strategy_id,capital_base)`. Twelve JSONB metric columns:
four policies × daily/weekly/monthly. Each contains status, initial/final capital,
net profit, compound return, trades/wins/win rate/MDD/rank; available outputs also
contain normal/EOD exits, overnight, maximum quantity, zero-share skips, open count.
No detailed trade copies. Master definition is captured for historical display.

`flow_v3_leadership_run` is the atomic publication manifest, including source audit,
version and deterministic result hash. A transaction-level date-specific advisory
lock prevents concurrent publication. Identical reruns are no-ops; changed results
for an already published date raise `IMMUTABLE_SNAPSHOT_CONFLICT`. No automatic
UPDATE to historical snapshots. Manifest and complete rows commit together.

Default global ordinal rank: return DESC, net DESC, strategy_id ASC. Missing results
are unranked. Filters never shrink the calculation universe. Dashboard Top50/100 is
selected in SQL, not by downloading all rows. Previous rank compares the immediately
preceding published date. History is at most 31 dates × 100 selected strategies.

At the current active universe/capital count: 57,600 rows/day, 14,400,000/250 days.
These are estimates, not runtime constants. Capacity/storage timing must be measured
on a production-sized **isolated** copy before deployment; no production full replay
was requested or executed.

## Local commands / future operations (not run in production)

1. Apply new migration only after separate approval. Provision dedicated reader
   (SELECT on required sources/new results) and writer (SELECT/INSERT only on two
   new tables; no source DML, schema CREATE, ownership or superuser). Roles/secrets
   are deliberately not created by the migration.
2. Set `LEADERSHIP_READ_DSN`. Optional dry run:
   `python scripts/research/run_flow_v3_leadership.py --date YYYY-MM-DD`
3. Explicit `LEADERSHIP_WRITE_DSN` plus `--write-snapshot` publishes only new results.
4. `python scripts/dashboard/serve_flow_v3_leadership.py --port 8094`
   serves `/flow-v3-leadership.html` and `/leadership/api/{options,ranking,history}`.
5. Proposed independent timer: 21:15 Asia/Seoul, persistent, no trading dependencies.
   The batch additionally verifies the configured extended session has ended.
   Resource controls and failure affect this unit only. Dedicated env files are
   required; existing trading services/env files are never reused or changed.
6. Dashboard binds loopback by default. Separate access control/reverse-proxy setup
   is a deployment decision. It has no POST endpoints and receives no writer DSN.

## Remaining production readiness checks

- Complete extended price source/venue and same-day EOD actual-bar availability.
- Complete 09:00 warmup history and extended coverage for HOLD research span.
- Production-sized isolated replay timing/memory and realistic holiday coverage.
- Dedicated least-privilege DSNs, timer host/timezone, UI ingress/authentication.
- Review period realized-PnL attribution and extended EOD reverse-search contract
  documented above before interpreting results as a final research benchmark.

## Local validation result

- 30 unittest cases PASS, including isolated PostgreSQL migration/reapplication,
  source-query execution, dynamic capitals, immutable publication/rollback, reader
  transaction, restart connection, Top50/100 API and HTTP 200 checks.
- Combined Leadership + existing FLOW runtime/accounting/correction/live-contract/
  SEND-authorization unit regression: **80 PASS / 0 SKIP / 0 FAIL**. Python compile PASS.
- JS syntax PASS. Existing tracked files unchanged; only new Leadership files.
- Visual mobile/chart QA **PENDING**: connected UI inventory returned
  `apps=[]`, `browsers=[]`. A local fixture-backed HTTP preview was available;
  no production Dashboard was opened or modified. CSS/mobile/source checks are
  not represented as completed browser QA.
- Production migration/DML, unit installation/restart, KIS POST and push: NONE.
