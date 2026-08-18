import unittest
from datetime import datetime

from src.broker.contracts import BrokerOrder, BrokerOrderStatus
from src.collector.raw.kis_order_transport import (
    LIVE_CASH_BUY_TR_ID, LIVE_CASH_SELL_TR_ID,
    KISOrderPostTransport, KISOrderTransportConfig,
)
from src.smoke_send import ActualApproval, ApprovalStatus
from src.smoke_send.authorization import _context_from_consumed_approval, issue_transport_permit
from src.smoke_gate import SMOKE_OWNERSHIP_ID


def order(*, side="BUY", qty=1, phase="7C-1", code="0193W0", exchange="KRX", division="15", price="0"):
    return BrokerOrder(
        "broker-1", "approval-1", SMOKE_OWNERSHIP_ID, code, side, qty,
        "key-1", BrokerOrderStatus.SUBMITTING,
        {"phase": phase, "EXCG_ID_DVSN_CD": exchange, "ORD_DVSN": division, "ORD_UNPR": price},
        created_at=datetime(2026, 8, 19, 10),
    )


class Client:
    def __init__(self, response): self.response, self.calls = response, []
    def post_once(self, **kwargs): self.calls.append(kwargs); return self.response


class KISOrderPostTransportTest(unittest.TestCase):
    def transport(self, response={"rt_cd": "0", "output": {"ODNO": "A"}}):
        client = Client(response)
        config = KISOrderTransportConfig(
            "12345678", "01", LIVE_CASH_BUY_TR_ID, LIVE_CASH_SELL_TR_ID,
            frozenset({"0193W0", "0193L0", "0197X0"}),
        )
        return KISOrderPostTransport(client=client, config=config), client

    @staticmethod
    def permit(current_order):
        approval = ActualApproval(
            "approval-1", SMOKE_OWNERSHIP_ID, "0193W0", datetime(2026, 8, 19).date(),
            datetime.strptime("10:00", "%H:%M").time(), datetime.strptime("10:10", "%H:%M").time(),
            ApprovalStatus.CONSUMED, "key-1",
        )
        return issue_transport_permit(_context_from_consumed_approval(approval), current_order)

    def test_maps_runtime_authorized_order_to_post_without_credentials_in_audit(self):
        transport, client = self.transport()
        current_order = order()
        response = transport.submit_once(current_order, permit=self.permit(current_order))
        self.assertEqual(response["rt_cd"], "0")
        self.assertEqual(transport.invocation_count, 1)
        self.assertEqual(transport.actual_post_send_count, 1)
        self.assertEqual(client.calls[0]["path"], "/uapi/domestic-stock/v1/trading/order-cash")
        self.assertEqual(client.calls[0]["tr_id"], LIVE_CASH_BUY_TR_ID)
        self.assertEqual(client.calls[0]["payload"]["ORD_QTY"], "1")
        self.assertEqual(client.calls[0]["payload"]["ORD_DVSN"], "15")
        self.assertEqual(client.calls[0]["payload"]["ORD_UNPR"], "0")
        self.assertEqual(client.calls[0]["payload"]["EXCG_ID_DVSN_CD"], "KRX")
        self.assertEqual(client.calls[0]["custtype"], "P")
        self.assertEqual(transport.audit[-1]["response_classification"], "ACK_ACCEPTED")

    def test_direct_or_tampered_transport_calls_never_post(self):
        transport, client = self.transport({"rt_cd": "1", "msg_cd": "REJECT"})
        current_order = order()
        with self.assertRaises(Exception):
            transport.submit_once(current_order)
        permit = self.permit(current_order)
        for invalid in (
            order(qty=2), order(side="SELL"), order(code="0197X0"),
            order(phase="OTHER"), order(code="BAD"), order(exchange="NXT"),
            order(exchange="SOR"), order(division="01"), order(division="00"),
            order(price="1000"),
        ):
            with self.assertRaises(Exception):
                transport.submit_once(invalid, permit=permit)
        self.assertEqual(len(client.calls), 0)
