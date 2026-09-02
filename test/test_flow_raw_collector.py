from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.flow_raw.collector import FlowRawCollector
from src.minute_ma.integrated_realtime_contracts import TR_INTEGRATED_EXECUTION
from src.flow_raw.contracts import (
    EXECUTION_FIELDS, ORDERBOOK_FIELDS, ORDERBOOK_GATEWAY_62_FIELDS, PROGRAM_FIELDS, TR_EXECUTION, TR_ORDERBOOK,
    TR_PROGRAM, FlowContractError, five_second_bucket, source_datetime, split_wire_frame,
)


def frame(tr_id: str, names: tuple[str, ...], records: list[dict[str, str]]) -> str:
    body = []
    for record in records:
        body.extend(record.get(name, "0") for name in names)
    return f"0|{tr_id}|{len(records):03d}|" + "^".join(body)


class FlowContractTest(unittest.TestCase):
    def test_official_field_widths_are_locked(self):
        self.assertEqual(len(EXECUTION_FIELDS), 46)
        self.assertEqual(len(PROGRAM_FIELDS), 11)
        self.assertEqual(len(ORDERBOOK_FIELDS), 59)

    def test_multiple_execution_records_keep_event_order(self):
        raw = frame(TR_EXECUTION, EXECUTION_FIELDS, [
            {"MKSC_SHRN_ISCD": "005930", "STCK_CNTG_HOUR": "091500", "BSOP_DATE": "20260831"},
            {"MKSC_SHRN_ISCD": "000660", "STCK_CNTG_HOUR": "091501", "BSOP_DATE": "20260831"},
        ])
        parsed = split_wire_frame(raw)
        self.assertEqual([item.event_index for item in parsed], [0, 1])
        self.assertEqual([item.values["MKSC_SHRN_ISCD"] for item in parsed], ["005930", "000660"])
        self.assertLess(source_datetime(parsed[0], received_at=datetime(2026, 8, 31, 9, 15, 2)),
                        source_datetime(parsed[1], received_at=datetime(2026, 8, 31, 9, 15, 2)))

    def test_program_and_orderbook_are_lossless(self):
        program = split_wire_frame(frame(TR_PROGRAM, PROGRAM_FIELDS, [{
            "MKSC_SHRN_ISCD": "005930", "STCK_CNTG_HOUR": "091501", "NTBY_TR_PBMN": "-12345",
        }]))[0]
        orderbook = split_wire_frame(frame(TR_ORDERBOOK, ORDERBOOK_FIELDS, [{
            "MKSC_SHRN_ISCD": "005930", "BSOP_HOUR": "091504", "ASKP10": "80000", "BIDP10": "70000",
        }]))[0]
        self.assertEqual(program.values["NTBY_TR_PBMN"], "-12345")
        self.assertEqual(orderbook.values["ASKP10"], "80000")
        self.assertEqual(orderbook.values["BIDP10"], "70000")

    def test_naive_kst_received_date_is_not_advanced_after_1500(self):
        program = split_wire_frame(frame(TR_PROGRAM, PROGRAM_FIELDS, [{
            "MKSC_SHRN_ISCD": "000660", "STCK_CNTG_HOUR": "150004",
        }]))[0]
        orderbook = split_wire_frame(frame(TR_ORDERBOOK, ORDERBOOK_FIELDS, [{
            "MKSC_SHRN_ISCD": "005930", "BSOP_HOUR": "152955",
        }]))[0]
        received = datetime(2026, 8, 31, 15, 0, 4)
        self.assertEqual(source_datetime(program, received_at=received),
                         datetime(2026, 8, 31, 15, 0, 4))
        self.assertEqual(source_datetime(orderbook, received_at=received),
                         datetime(2026, 8, 31, 15, 29, 55))

    def test_aware_received_time_is_converted_to_kst_date(self):
        program = split_wire_frame(frame(TR_PROGRAM, PROGRAM_FIELDS, [{
            "MKSC_SHRN_ISCD": "000660", "STCK_CNTG_HOUR": "000004",
        }]))[0]
        received_utc = datetime(2026, 8, 31, 15, 0, 4, tzinfo=timezone.utc)
        self.assertEqual(source_datetime(program, received_at=received_utc),
                         datetime(2026, 9, 1, 0, 0, 4))

    def test_gateway_62_field_orderbook_keeps_official_core_and_trailing_values(self):
        orderbook = split_wire_frame(frame(TR_ORDERBOOK, ORDERBOOK_GATEWAY_62_FIELDS, [{
            "MKSC_SHRN_ISCD": "005930", "BSOP_HOUR": "091504",
            "ASKP1": "80000", "BIDP1": "79900",
            "KIS_UNDOCUMENTED_FIELD_60": "x60",
            "KIS_UNDOCUMENTED_FIELD_61": "x61",
            "KIS_UNDOCUMENTED_FIELD_62": "x62",
        }]))[0]
        self.assertEqual(orderbook.values["ASKP1"], "80000")
        self.assertEqual(orderbook.values["BIDP1"], "79900")
        self.assertEqual(orderbook.values["KIS_UNDOCUMENTED_FIELD_60"], "x60")
        self.assertEqual(orderbook.values["KIS_UNDOCUMENTED_FIELD_62"], "x62")

    def test_gateway_62_field_multi_record_order_is_preserved(self):
        parsed = split_wire_frame(frame(TR_ORDERBOOK, ORDERBOOK_GATEWAY_62_FIELDS, [
            {"MKSC_SHRN_ISCD": "005930", "BSOP_HOUR": "091504"},
            {"MKSC_SHRN_ISCD": "000660", "BSOP_HOUR": "091505"},
        ]))
        self.assertEqual([item.event_index for item in parsed], [0, 1])
        self.assertEqual([item.values["MKSC_SHRN_ISCD"] for item in parsed], ["005930", "000660"])

    def test_bad_width_fails_closed(self):
        with self.assertRaises(FlowContractError):
            split_wire_frame("0|H0STCNT0|001|005930^091500")

    def test_five_second_bucket(self):
        self.assertEqual(five_second_bucket(datetime(2026, 8, 31, 9, 15, 9, 999)),
                         datetime(2026, 8, 31, 9, 15, 5))

    def test_duplicate_identity_and_exact_subscription_scope(self):
        collector = FlowRawCollector(object(), integrated_repository=object(),
                                     ws_url="ws://example", approval_provider=lambda: "x")
        identity = (TR_EXECUTION, "005930", "hash")
        self.assertFalse(collector._remember_hash(identity))
        self.assertTrue(collector._remember_hash(identity))
        self.assertEqual({item["tr_key"] for item in collector.subscriptions},
                         {"005930", "000660", "0193W0", "0193T0", "0193L0", "0197X0"})
        self.assertEqual({item["tr_id"] for item in collector.subscriptions},
                         {TR_EXECUTION, TR_PROGRAM, TR_ORDERBOOK,TR_INTEGRATED_EXECUTION})
        self.assertEqual(len(collector.subscriptions), 12)
        self.assertEqual(sum(item["tr_id"] == TR_EXECUTION for item in collector.subscriptions), 6)
        self.assertEqual(sum(item["tr_id"] in {TR_PROGRAM,TR_ORDERBOOK}
                             for item in collector.subscriptions),4)
        self.assertEqual(sum(item["tr_id"] == TR_INTEGRATED_EXECUTION
                             for item in collector.subscriptions),2)

    def test_l0_collector_has_no_l1_rebuild_call(self):
        source = Path("src/flow_raw/collector.py").read_text(encoding="utf-8")
        self.assertNotIn("refresh_l1", source)


if __name__ == "__main__":
    unittest.main()
