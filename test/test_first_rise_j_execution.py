from datetime import timedelta
from decimal import Decimal

import pytest

from src.first_rise_breakout.j_execution import STRATEGY_ID, plan_entry, owned_sell_quantity
from test.test_first_rise_j import CONFIG, at


def plan(**changes):
    args=dict(config=CONFIG,signal_time=at(9,30),signal_sequence=1,
        prior_exit_reason=None,prior_exit_time=None,effective_from=at(9,0),
        now=at(9,31),realized_net_pnl=0,broker_cash=25000000,price=10000,
        buy_fee_rate=0,same_stock_pending_or_open=False)
    args.update(changes)
    return plan_entry(**args)


def test_no_fixed_total_allocation_and_shared_cash_recovery():
    assert plan().quantity == 1000
    assert plan(broker_cash=7000000).quantity == 700
    assert plan(broker_cash=0).reason == 'NO_CAPITAL'
    assert plan(broker_cash=25000000).quantity == 1000


def test_unacknowledged_claim_cannot_reuse_cash():
    assert plan(broker_cash=10000000,unacknowledged_reservation=10000000).quantity == 0
    assert plan(broker_cash=15000000,unacknowledged_reservation=10000000).quantity == 500


def test_no_replay_and_no_same_stock_pyramiding():
    assert plan(effective_from=at(9,31)).reason == 'NO_REPLAY'
    assert plan(same_stock_pending_or_open=True).reason == 'SAME_STOCK_PENDING_OR_OPEN'
    assert plan(same_stock_pending_or_open=True,signal_sequence=2,signal_time=at(10,0),now=at(10,1),
        prior_exit_reason='STOP_ENTRY_BREAK',prior_exit_time=at(9,55)).quantity==1000
    assert plan(signal_time=at(9,30)-timedelta(days=1)).quantity == 0


def test_second_requires_independent_stop_not_actual_reject():
    assert plan(signal_time=at(10,1),now=at(10,2)).reason == 'FIRST_AFTER_1000'
    for reason in ('REJECTED','NO_CAPITAL','OVERLAP_SKIP','BOOK_TENKAN_PROFIT','SESSION_CLOSE'):
        assert plan(signal_sequence=2,signal_time=at(10,0),now=at(10,1),
            prior_exit_reason=reason,prior_exit_time=at(9,55)).reason == 'SECOND_NOT_ELIGIBLE'
    assert plan(signal_sequence=2,signal_time=at(10,0),now=at(10,1),
        prior_exit_reason='STOP_ENTRY_BREAK',prior_exit_time=at(9,55)).quantity == 1000
    assert plan(signal_sequence=3).reason == 'THIRD_FORBIDDEN'


def test_ownership_never_uses_account_total():
    args=dict(strategy_id=STRATEGY_ID,position_strategy_id=STRATEGY_ID,
        operation_id='J1',position_operation_id='J1',trade_id='T1',position_trade_id='T1',
        owned_quantity=10,pending_sell_quantity=0)
    assert owned_sell_quantity(**args) == 10
    assert owned_sell_quantity(**{**args,'pending_sell_quantity':4}) == 6
    for replacement in ({'position_strategy_id':'MINUTE_MA'},
                        {'position_operation_id':'J2'}, {'position_trade_id':'T2'}):
        with pytest.raises(ValueError):owned_sell_quantity(**{**args,**replacement})


@pytest.mark.parametrize('cash',[Decimal('NaN'),Decimal('Infinity'),Decimal('-1')])
def test_invalid_cash_never_submits(cash):
    with pytest.raises(ValueError):plan(broker_cash=cash)
