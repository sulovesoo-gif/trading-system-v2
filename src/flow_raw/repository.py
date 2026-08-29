"""PostgreSQL persistence for FLOW websocket L0 and rebuildable L1 bars."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from .contracts import TR_EXECUTION, TR_ORDERBOOK, TR_PROGRAM, WireEvent, as_decimal, as_int, five_second_bucket, source_datetime


class FlowRawRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    def open_connection(self, *, connection_id: UUID, collector_instance_id: UUID,
                        connected_at: datetime, reconnect_flag: bool, subscriptions: list[dict]) -> None:
        with self.pool.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO flow_ws_connection
                   (connection_id,collector_instance_id,connected_at,reconnect_flag,status,subscriptions)
                   VALUES (%s,%s,%s,%s,'CONNECTED',%s)""",
                (connection_id, collector_instance_id, connected_at, reconnect_flag, Jsonb(subscriptions)),
            )

    def close_connection(self, connection_id: UUID, *, disconnected_at: datetime,
                         status: str, reason: str, last_sequence: int) -> None:
        with self.pool.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """UPDATE flow_ws_connection SET disconnected_at=%s,status=%s,close_reason=%s,
                          last_receive_sequence=%s WHERE connection_id=%s""",
                (disconnected_at, status, reason[:2000], last_sequence, connection_id),
            )

    def payload_seen(self, *, tr_id: str, stock_code: str, payload_hash: str,
                     received_at: datetime) -> bool:
        table = {TR_EXECUTION: "raw_flow_execution", TR_PROGRAM: "raw_flow_program", TR_ORDERBOOK: "raw_flow_orderbook_5s"}[tr_id]
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT EXISTS (SELECT 1 FROM {table} WHERE stock_code=%s AND payload_hash=%s AND received_at >= %s)",
                (stock_code, payload_hash, received_at - timedelta(minutes=10)),
            )
            return bool(cursor.fetchone()[0])

    def recent_hashes(self, *, since: datetime) -> set[tuple[str, str, str]]:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT tr_id,stock_code,payload_hash FROM raw_flow_execution WHERE received_at >= %s
                   UNION SELECT tr_id,stock_code,payload_hash FROM raw_flow_program WHERE received_at >= %s
                   UNION SELECT tr_id,stock_code,payload_hash FROM raw_flow_orderbook_5s WHERE received_at >= %s""",
                (since, since, since),
            )
            return {(row[0], row[1], row[2]) for row in cursor.fetchall()}

    def save_event(self, event: WireEvent, *, received_at: datetime, connection_id: UUID,
                   collector_instance_id: UUID, receive_sequence: int, reconnect_flag: bool,
                   source_gap_flag: bool, event_time_regression_flag: bool, duplicate_flag: bool) -> None:
        source_time = source_datetime(event, received_at=received_at)
        common = (
            received_at, source_time, source_time.date(), event.values.get("MKSC_SHRN_ISCD"), "KRX",
            event.tr_id, connection_id, collector_instance_id, receive_sequence, event.event_index,
            reconnect_flag, source_gap_flag, event_time_regression_flag, duplicate_flag,
            event.payload_hash,
        )
        values = event.values
        with self.pool.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            if event.tr_id == TR_EXECUTION:
                cursor.execute(
                    """INSERT INTO raw_flow_execution
                       (received_at,source_event_time,business_date,stock_code,trading_venue,tr_id,
                        connection_id,collector_instance_id,receive_sequence,event_index,reconnect_flag,
                        source_gap_flag,event_time_regression_flag,duplicate_flag,payload_hash,current_price,
                        execution_volume,accumulated_volume,accumulated_amount,sell_execution_count,
                        buy_execution_count,net_buy_execution_count,execution_strength,total_sell_quantity,
                        total_buy_quantity,execution_classification,buy_ratio,ask_price_1,bid_price_1,
                        ask_quantity_1,bid_quantity_1,total_ask_quantity,total_bid_quantity,
                        raw_values,raw_payload)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                               %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    common + (
                        as_int(values.get("STCK_PRPR")), as_int(values.get("CNTG_VOL")),
                        as_int(values.get("ACML_VOL")), as_decimal(values.get("ACML_TR_PBMN")),
                        as_int(values.get("SELN_CNTG_CSNU")), as_int(values.get("SHNU_CNTG_CSNU")),
                        as_int(values.get("NTBY_CNTG_CSNU")), as_decimal(values.get("CTTR")),
                        as_int(values.get("SELN_CNTG_SMTN")), as_int(values.get("SHNU_CNTG_SMTN")),
                        values.get("CCLD_DVSN"), as_decimal(values.get("SHNU_RATE")),
                        as_int(values.get("ASKP1")), as_int(values.get("BIDP1")),
                        as_int(values.get("ASKP_RSQN1")), as_int(values.get("BIDP_RSQN1")),
                        as_int(values.get("TOTAL_ASKP_RSQN")), as_int(values.get("TOTAL_BIDP_RSQN")),
                        Jsonb(values), event.raw_record,
                    ),
                )
            elif event.tr_id == TR_PROGRAM:
                cursor.execute(
                    """INSERT INTO raw_flow_program
                       (received_at,source_event_time,business_date,stock_code,trading_venue,tr_id,
                        connection_id,collector_instance_id,receive_sequence,event_index,reconnect_flag,
                        source_gap_flag,event_time_regression_flag,duplicate_flag,payload_hash,
                        sell_execution_quantity,sell_execution_amount,buy_execution_quantity,
                        buy_execution_amount,net_buy_execution_quantity,net_buy_execution_amount,
                        sell_orderbook_quantity,buy_orderbook_quantity,total_net_buy_orderbook_quantity,
                        raw_values,raw_payload)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                               %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    common + (
                        as_int(values.get("SELN_CNTG_VOL")), as_decimal(values.get("SELN_TR_PBMN")),
                        as_int(values.get("SHNU_CNTG_VOL")), as_decimal(values.get("SHNU_TR_PBMN")),
                        as_int(values.get("NTBY_CNTG_VOL")), as_decimal(values.get("NTBY_TR_PBMN")),
                        as_int(values.get("SELN_RSQN")), as_int(values.get("SHNU_RSQN")),
                        as_int(values.get("WHOL_NTBY_RSQN")), Jsonb(values), event.raw_record,
                    ),
                )
            else:
                asks = [as_int(values.get(f"ASKP{i}")) or 0 for i in range(1, 11)]
                bids = [as_int(values.get(f"BIDP{i}")) or 0 for i in range(1, 11)]
                ask_qty = [as_int(values.get(f"ASKP_RSQN{i}")) or 0 for i in range(1, 11)]
                bid_qty = [as_int(values.get(f"BIDP_RSQN{i}")) or 0 for i in range(1, 11)]
                midpoint = Decimal(asks[0] + bids[0]) / 2 if asks[0] and bids[0] else None
                cursor.execute(
                    """INSERT INTO raw_flow_orderbook_5s
                       (bucket_start,source_event_time,received_at,stock_code,trading_venue,tr_id,
                        connection_id,collector_instance_id,receive_sequence,reconnect_flag,source_gap_flag,
                        event_time_regression_flag,duplicate_flag,payload_hash,ask_prices,bid_prices,
                        ask_quantities,bid_quantities,total_ask_quantity,total_bid_quantity,
                        total_ask_quantity_change,total_bid_quantity_change,midpoint,raw_values,raw_payload)
                       VALUES (%s,%s,%s,%s,'KRX',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (bucket_start,stock_code,trading_venue) DO UPDATE SET
                         source_event_time=EXCLUDED.source_event_time,received_at=EXCLUDED.received_at,
                         connection_id=EXCLUDED.connection_id,collector_instance_id=EXCLUDED.collector_instance_id,
                         receive_sequence=EXCLUDED.receive_sequence,reconnect_flag=EXCLUDED.reconnect_flag,
                         source_gap_flag=EXCLUDED.source_gap_flag,event_time_regression_flag=EXCLUDED.event_time_regression_flag,
                         duplicate_flag=EXCLUDED.duplicate_flag,payload_hash=EXCLUDED.payload_hash,
                         ask_prices=EXCLUDED.ask_prices,bid_prices=EXCLUDED.bid_prices,
                         ask_quantities=EXCLUDED.ask_quantities,bid_quantities=EXCLUDED.bid_quantities,
                         total_ask_quantity=EXCLUDED.total_ask_quantity,total_bid_quantity=EXCLUDED.total_bid_quantity,
                         total_ask_quantity_change=EXCLUDED.total_ask_quantity_change,
                         total_bid_quantity_change=EXCLUDED.total_bid_quantity_change,midpoint=EXCLUDED.midpoint,
                         raw_values=EXCLUDED.raw_values,raw_payload=EXCLUDED.raw_payload""",
                    (
                        five_second_bucket(source_time), source_time, received_at,
                        values.get("MKSC_SHRN_ISCD"), event.tr_id, connection_id, collector_instance_id,
                        receive_sequence, reconnect_flag, source_gap_flag, event_time_regression_flag,
                        duplicate_flag, event.payload_hash, asks, bids, ask_qty, bid_qty,
                        as_int(values.get("TOTAL_ASKP_RSQN")), as_int(values.get("TOTAL_BIDP_RSQN")),
                        as_int(values.get("TOTAL_ASKP_RSQN_ICDC")), as_int(values.get("TOTAL_BIDP_RSQN_ICDC")),
                        midpoint, Jsonb(values), event.raw_record,
                    ),
                )

    def refresh_l1(self, *, now: datetime) -> None:
        """Atomically rebuild recent closed 5-second and 1-minute bars from L0."""
        cutoff = now.replace(microsecond=0)
        start = cutoff - timedelta(minutes=3)
        with self.pool.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute("SELECT rebuild_flow_bars(%s,%s)", (start, cutoff))
