"""FLOW-only live contracts. No authorization or broker submission side effects."""
from datetime import time
from decimal import Decimal, ROUND_FLOOR

LONG_IDS = ('FV3008243','FV3008241','FV3009185','FV3009201','FV3009187',
            'FV3009203','FV3008227','FV3008225','FV3008211','FV3008209','FV3008084')
SHORT_IDS = ('FV3005688','FV3005672','FV3004728','FV3004712')
WHITELIST = {**{s: ('LONG','0193T0') for s in LONG_IDS},
             **{s: ('SHORT','0197X0') for s in SHORT_IDS}}
CUTOFF = time(15,18)
EOD_EXECUTION = time(15,19)


def validate_mapping(strategy_id, stock_code, direction, execution_code):
    if stock_code != '000660' or WHITELIST.get(strategy_id) != (direction, execution_code):
        raise ValueError('FLOW_LIVE_WHITELIST_MAPPING_REJECTED')


def order_quantity(capital, price):
    capital, price = Decimal(capital), Decimal(price)
    if not capital.is_finite() or not price.is_finite() or price <= 0:
        raise ValueError('FLOW_LIVE_REFERENCE_OR_CAPITAL_INVALID')
    return max(0, int((capital / price).to_integral_value(rounding=ROUND_FLOOR)))


def request_payload(code, side, quantity):
    if code not in ('0193T0','0197X0') or side not in ('BUY','SELL') or quantity <= 0:
        raise ValueError('FLOW_LIVE_REQUEST_INVALID')
    # Account identity is deliberately not persisted. Market policy matches the
    # existing verified KRX cash-order adapter; it is NOT a PAPER proxy price.
    body = dict(PDNO=code, ORD_DVSN='01', ORD_QTY=str(quantity), ORD_UNPR='0',
                EXCG_ID_DVSN_CD='KRX')
    if side == 'SELL':
        body['SLL_TYPE'] = '01'
    return dict(endpoint='/uapi/domestic-stock/v1/trading/order-cash',
                tr_id='TTTC0012U' if side == 'BUY' else 'TTTC0011U', body=body)


def normal_exit(event, state):
    """Lot's frozen contract, not the current strategy operation flag."""
    from .engine import PAIR_CODE
    if not state['is_complete'] or state['bar_time'] <= event['entry_signal_time']:
        return False
    if event['exit_policy_code'] == 'SIGNAL_EOD':
        if state['bar_time'].date() != event['entry_signal_time'].date() or state['bar_time'].time() > CUTOFF:
            return False
    pair = PAIR_CODE[(event['exit_fast_period'], event['exit_slow_period'])]
    crosses = state['velocity_crosses'] if event['entry_family_code'] == 'F2' else state['flow_crosses']
    return crosses.get(pair) == (-1 if event['direction'] == 'LONG' else 1)


def cumulative_delta(old_quantity, old_amount, quantity, amount, requested):
    amount, old_amount = Decimal(amount), Decimal(old_amount)
    if not amount.is_finite() or quantity < old_quantity or amount < old_amount or quantity > requested:
        raise ValueError('BROKER_CUMULATIVE_REGRESSION_OR_OVERFILL')
    dq, da = quantity-old_quantity, amount-old_amount
    if (dq == 0) != (da == 0) or dq < 0 or da < 0:
        raise ValueError('BROKER_CUMULATIVE_INCONSISTENT')
    return dq, da


def cancel_payload(order_number, branch, remaining):
    if not order_number or not branch or remaining<=0:
        raise ValueError('CANCEL_BROKER_IDENTITY_REQUIRED')
    return dict(endpoint='/uapi/domestic-stock/v1/trading/order-rvsecncl',tr_id='TTTC0013U',
        body=dict(KRX_FWDG_ORD_ORGNO=branch,ORGN_ODNO=order_number,ORD_DVSN='01',
                  RVSE_CNCL_DVSN_CD='02',ORD_QTY=str(remaining),ORD_UNPR='0',
                  QTY_ALL_ORD_YN='Y',EXCG_ID_DVSN_CD='KRX'))
