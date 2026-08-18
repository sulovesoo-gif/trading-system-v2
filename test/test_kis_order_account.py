import unittest

from src.collector.raw.kis_order_account import (
    KISOrderAccount,
    KISOrderAccountConfigurationError,
)
from src.collector.raw.kis_order_transport import (
    LIVE_CASH_BUY_TR_ID, LIVE_CASH_SELL_TR_ID, KISOrderTransportConfig,
)


class KISOrderAccountTest(unittest.TestCase):
    def test_explicit_environment_values_are_required_without_parsing_or_defaults(self):
        account = KISOrderAccount.from_environment({
            "KIS_ACC_NO": "12345678", "KIS_ACNT_PRDT_CD": "01",
        })
        self.assertEqual(account.cano, "12345678")
        self.assertEqual(account.account_product_code, "01")
        self.assertEqual(account.custtype, "P")
        for values in (
            {}, {"KIS_ACC_NO": "12345678"},
            {"KIS_ACC_NO": "1234-5678", "KIS_ACNT_PRDT_CD": "01"},
            {"KIS_ACC_NO": "12345678", "KIS_ACNT_PRDT_CD": "1"},
        ):
            with self.assertRaises(KISOrderAccountConfigurationError):
                KISOrderAccount.from_environment(values)

    def test_transport_configuration_uses_resolver_and_official_live_tr_ids(self):
        from unittest.mock import patch
        with patch.dict("os.environ", {"KIS_ACC_NO": "12345678", "KIS_ACNT_PRDT_CD": "01"}, clear=True):
            config = KISOrderTransportConfig.from_environment(whitelist=frozenset({"0193W0"}))
        self.assertEqual(config.account_number, "12345678")
        self.assertEqual(config.account_product_code, "01")
        self.assertEqual(config.custtype, "P")
        self.assertEqual(config.buy_tr_id, LIVE_CASH_BUY_TR_ID)
        self.assertEqual(config.sell_tr_id, LIVE_CASH_SELL_TR_ID)


if __name__ == "__main__":
    unittest.main()
