from __future__ import annotations

import unittest
from datetime import datetime

from src.flow_raw.collector import FlowRawCollector
from src.flow_raw.contracts import (
    EXECUTION_FIELDS, ORDERBOOK_FIELDS, PROGRAM_FIELDS, TR_EXECUTION, TR_ORDERBOOK,
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

    def test_bad_width_fails_closed(self):
        with self.assertRaises(FlowContractError):
            split_wire_frame("0|H0STCNT0|001|005930^091500")

    def test_five_second_bucket(self):
        self.assertEqual(five_second_bucket(datetime(2026, 8, 31, 9, 15, 9, 999)),
                         datetime(2026, 8, 31, 9, 15, 5))

    def test_duplicate_identity_and_exact_subscription_scope(self):
        collector = FlowRawCollector(object(), ws_url="ws://example", approval_provider=lambda: "x")
        identity = (TR_EXECUTION, "005930", "hash")
        self.assertFalse(collector._remember_hash(identity))
        self.assertTrue(collector._remember_hash(identity))
        self.assertEqual({item["tr_key"] for item in collector.subscriptions}, {"005930", "000660"})
        self.assertEqual({item["tr_id"] for item in collector.subscriptions}, {TR_EXECUTION, TR_PROGRAM, TR_ORDERBOOK})
        self.assertEqual(len(collector.subscriptions), 6)


if __name__ == "__main__":
    unittest.main()
