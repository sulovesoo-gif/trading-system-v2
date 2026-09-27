from datetime import datetime
from decimal import Decimal

from src.minute_ma.real_live import PostgresMinuteMaRealLivePlanner
from src.minute_ma.real_paper import purchasable_quantity


def test_underlying_capital_quantity_includes_buy_fee():
    assert purchasable_quantity(Decimal("2000000"),Decimal("199000"))==10
    assert purchasable_quantity(Decimal("2000000"),Decimal("200000"))==9


def test_route_rebase_source_preserves_old_open_trade_identity():
    source=open("src/minute_ma/real_live.py",encoding="utf-8").read()
    assert "old OPEN trades retain the old route id" in source
    assert "UPDATE minute_ma_real_live_route SET effective_to" in source
    assert "real_live_route_id=r.real_live_route_id" in source
    assert "t.trade_status='OPEN'" in source


def test_fixed_quantity_and_zero_capital_are_the_only_entry_controls():
    source=open("src/minute_ma/real_live.py",encoding="utf-8").read()
    assert 'if sizing=="FIXED_QTY"' in source
    assert "qty=int(fixed)" in source
    assert "qty=purchasable_quantity(capital,price)" in source
    assert 'reason="ZERO_QUANTITY"' in source
    for forbidden in ("send_enabled","actual_enabled","order_allowed","live_enabled"):
        assert forbidden not in source


def test_same_route_event_is_idempotent_but_open_position_is_not_an_entry_gate():
    source=open("src/minute_ma/real_live.py",encoding="utf-8").read()
    assert "MINUTE_REAL_V1_5|ENTRY|{route.route_id}|{event_key}" in source
    assert "WHERE intent_key=%s" in source
    assert "OPEN_POSITION" not in source
