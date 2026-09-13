"""FLOW account pre-order checks for every route; no synthetic deposit calculation."""
from decimal import Decimal
from src.broker.cash_lookup import KISBrokerAvailableCashLookup


def check_buy(route, required_amount, orderable_cash):
    if route not in ('UNDERLYING','LEVERAGE','INVERSE'):
        return 'UNSUPPORTED_ROUTE'
    needed=Decimal(required_amount)
    if not needed.is_finite() or needed<=0:
        return 'ORDER_AMOUNT_INVALID'
    if orderable_cash is None:
        return 'KIS_ORDERABLE_CASH_UNAVAILABLE'
    cash=Decimal(orderable_cash)
    if not cash.is_finite() or cash<0:
        return 'KIS_ORDERABLE_CASH_INVALID'
    if needed>cash:
        return 'KIS_ORDERABLE_CASH_INSUFFICIENT'
    return None


class FlowCashCheck:
    def __init__(self, client, account):
        self.lookup=KISBrokerAvailableCashLookup(client=client,account=account)

    def __call__(self, code, route, price, quantity):
        try:
            cash=self.lookup.orderable_cash(stock_code=code,order_price=Decimal(price),order_division='01')
            return check_buy(route,Decimal(price)*quantity,cash.amount)
        except Exception:
            return 'KIS_ORDERABLE_CASH_UNAVAILABLE'
