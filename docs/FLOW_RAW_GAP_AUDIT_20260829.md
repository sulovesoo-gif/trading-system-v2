# FLOW RAW GAP audit (2026-08-29)

## Existing production objects

| Object | Actual rows | Range | Finding |
|---|---:|---|---|
| `raw_stock_execution` | 1 | 2026-07-29 10:03:00.123 | REST snapshot; not individual websocket executions |
| `raw_program` | 3 | 2026-07-29 10:00:00.123–11:41:00.123 | REST 1MIN cumulative snapshot |
| `raw_market_investor` | 1 | 2026-07-29 10:01:00.123 | Market-wide REST auxiliary data |
| `raw_stock_quote` | 1 | 2026-07-29 10:02:00.123 | REST quote snapshot; no 10-level book |
| `raw_stock_minute` | 463,098 | 2025-08-11 09:00–2026-08-28 15:30 | Actual operating completed-minute source |
| `raw_market_flow` | absent | — | No reusable object |

All five existing RAW tables are Timescale hypertables. Existing REST objects remain unchanged.

## KIS field-to-storage classification

Source field order is locked to the official Korea Investment & Securities
`open-trading-api` websocket examples.

| Feed | Classification | Storage decision |
|---|---|---|
| H0STCNT0 price, volume, accumulated volume/amount, execution counts, strength, total buy/sell, classification, buy ratio | Existing names partially overlap, but semantics/cadence differ | Separate `raw_flow_execution`; all 46 source fields also retained in `raw_values` and `raw_payload` |
| H0STCNT0 1-level quote/quantity and session/VI fields | New columns or future-research fields | Parsed core columns plus lossless raw map/payload |
| H0STPGM0 11 fields | Existing REST columns resemble them but cumulative/increment provenance differs | Separate `raw_flow_program`; no collector-side delta conversion |
| H0STASP0 10-level prices/quantities, totals and changes | Existing quote object cannot represent book depth | Separate latest-state `raw_flow_orderbook_5s`; all 59 fields retained losslessly |
| connection/sequence/reconnect/gap/duplicate evidence | Absent from every existing RAW object | `flow_ws_connection` plus metadata on every L0 row |
| 5-second and 1-minute research aggregates | Absent | Rebuildable `flow_bar`; never used as L0 substitute |

L0 ingestion and L1 rebuilding are operationally isolated. `trading-flow-raw-collector.service`
does no aggregation work. `trading-flow-bar-aggregator.timer` starts the separate oneshot
`trading-flow-bar-aggregator.service`; aggregation failure cannot stop the websocket receive loop.

## Quality contract

- `receive_sequence` is monotonic inside one `connection_id`.
- Reconnect creates a new connection and marks its first accepted data event.
- Exact payload duplicates are preserved and flagged; L1 excludes duplicate executions/program rows.
- Event-time regression is durable evidence, not silently reordered.
- Irregular execution/program silence is treated as a legitimate zero-event interval; it is not
  guessed to be a disconnect. A greater-than-10-second gap in the high-cadence orderbook stream is
  marked as a source gap.
- A 5-second bar exists when a completed orderbook state exists. Its coverage is 1 or it is absent.
  A 1-minute bar coverage is the number of complete 5-second children divided by 12; only 12/12
  without gap/reconnect flags is `is_complete=true`.

## Official sources

- https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/domestic_stock/domestic_stock_functions_ws.py
- https://github.com/koreainvestment/open-trading-api/blob/main/legacy/websocket/python/ws_domestic_overseas_all.py
