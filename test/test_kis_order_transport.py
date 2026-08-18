import unittest
from datetime import datetime

from src.broker.contracts import BrokerOrder, BrokerOrderStatus
from src.collector.raw.kis_order_transport import KISOrderPostTransport, KISOrderTransportConfig


def order(*, side="BUY", qty=1, phase="7C-1", code="0193W0"):
    return BrokerOrder(
        "broker-1", "approval-1", "LIVE_STRATEGY_2", code, side, qty,
        "key-1", BrokerOrderStatus.SUBMITTING, {"phase": phase}, created_at=datetime(2026, 8, 19, 10),
    )


class Client:
    def __init__(self, response): self.response, self.calls = response, []
    def post_once(self, **kwargs): self.calls.append(kwargs); return self.response


class KISOrderPostTransportTest(unittest.TestCase):
    def transport(self, response={"rt_cd": "0", "output": {"ODNO": "A"}}):
        client = Client(response)
        config = KISOrderTransportConfig("12345678", "01", "BUY_TR", "SELL_TR", frozenset({"0193W0", "0193L0", "0197X0"}))
        return KISOrderPostTransport(client=client, config=config), client

    def test_maps_one_phase_order_to_post_without_credentials_in_audit(self):
        transport, client = self.transport()
        response = transport.submit_once(order())
        self.assertEqual(response["rt_cd"], "0")
        self.assertEqual(transport.invocation_count, 1)
        self.assertEqual(transport.actual_post_send_count, 1)
        self.assertEqual(client.calls[0]["path"], "/uapi/domestic-stock/v1/trading/order-cash")
        self.assertEqual(client.calls[0]["tr_id"], "BUY_TR")
        self.assertEqual(client.calls[0]["payload"]["ORD_QTY"], "1")
        self.assertEqual(transport.audit[-1]["response_classification"], "ACK_ACCEPTED")

    def test_reject_is_not_a_fill_and_invalid_order_never_posts(self):
        transport, client = self.transport({"rt_cd": "1", "msg_cd": "REJECT"})
        transport.submit_once(order())
        self.assertEqual(transport.audit[-1]["response_classification"], "ACK_REJECTED")
        for invalid in (order(qty=2), order(phase="OTHER"), order(code="BAD")):
            with self.assertRaises(Exception): transport.submit_once(invalid)
        self.assertEqual(len(client.calls), 1)
