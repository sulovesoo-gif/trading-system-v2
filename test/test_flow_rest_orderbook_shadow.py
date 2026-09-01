import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.observation.run_flow_rest_orderbook_shadow import (
    PATH,
    TR_ID,
    _append_jsonl,
    _bucket_start,
    _capture,
    _in_window,
)


class Client:
    def get(self, **kwargs):
        self.kwargs = kwargs
        return {
            "rt_cd": "0",
            "output1": {"askp1": "100", "bidp1": "99"},
            "output2": {"antc_cnpr": "101", "antc_cnqn": "7"},
        }


class FlowRestOrderbookShadowTest(unittest.TestCase):
    def test_official_window_is_1500_through_152955(self):
        kst = ZoneInfo("Asia/Seoul")
        self.assertTrue(_in_window(datetime(2026, 9, 2, 15, 0, tzinfo=kst)))
        self.assertTrue(_in_window(datetime(2026, 9, 2, 15, 29, 59, tzinfo=kst)))
        self.assertFalse(_in_window(datetime(2026, 9, 2, 14, 59, 59, tzinfo=kst)))
        self.assertFalse(_in_window(datetime(2026, 9, 2, 15, 30, tzinfo=kst)))

    def test_bucket_is_five_seconds(self):
        value = datetime(2026, 9, 2, 15, 20, 19, 999999, tzinfo=ZoneInfo("Asia/Seoul"))
        self.assertEqual(_bucket_start(value).second, 15)

    def test_capture_preserves_output_objects_and_identity(self):
        client = Client()
        row = _capture(client, "005930", datetime(2026, 9, 2, 15, 20, 5, tzinfo=ZoneInfo("Asia/Seoul")))
        self.assertEqual(client.kwargs["path"], PATH)
        self.assertEqual(client.kwargs["tr_id"], TR_ID)
        self.assertEqual(row["output1"]["askp1"], "100")
        self.assertEqual(row["output2"]["antc_cnpr"], "101")
        self.assertEqual(row["raw_source"], "KIS_REST_SHADOW")
        self.assertEqual(len(row["payload_hash"]), 64)

    def test_jsonl_is_separate_durable_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shadow.jsonl"
            _append_jsonl(path, [{"stock_code": "005930", "output1": {"askp1": "100"}}])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["stock_code"], "005930")


if __name__ == "__main__":
    unittest.main()
