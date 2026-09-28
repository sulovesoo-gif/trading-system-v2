from datetime import datetime
from decimal import Decimal

from src.minute_ma.real_live import actual_order_quantity,PostgresMinuteMaRealLivePlanner
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
    assert "actual_order_quantity(" in source
    assert '"ZERO_QUANTITY"' in source
    for forbidden in ("send_enabled","actual_enabled","order_allowed","live_enabled"):
        assert forbidden not in source


def test_capital_quantity_is_limited_by_available_cash():
    assert actual_order_quantity(sizing_mode="CAPITAL",current_capital=Decimal("2000"),
      fixed_quantity=0,reference_price=Decimal("900"),available_cash=Decimal("1000"))==(2,1,1)
    assert actual_order_quantity(sizing_mode="CAPITAL",current_capital=Decimal("2000"),
      fixed_quantity=0,reference_price=Decimal("900"),available_cash=Decimal("0"))==(2,0,0)
    assert actual_order_quantity(sizing_mode="CAPITAL",current_capital=Decimal("2000"),
      fixed_quantity=0,reference_price=Decimal("900"),available_cash=Decimal("5000"))==(2,5,2)


def test_fixed_quantity_is_preserved_when_cash_supports_one_share():
    assert actual_order_quantity(sizing_mode="FIXED_QTY",current_capital=Decimal("0"),
      fixed_quantity=1,reference_price=Decimal("900"),available_cash=Decimal("1000"))==(1,1,1)


def test_routes_query_uses_only_alias_qualified_joins():
    source=open("src/minute_ma/real_live.py",encoding="utf-8").read()
    routes_sql=source[source.index("def _routes"):source.index("def _bars")]
    assert "USING(" not in routes_sql
    assert "v.real_variant_id=r.real_variant_id" in routes_sql
    assert "p.minute_path_id=r.minute_path_id" in routes_sql
    assert "s.minute_strategy_id=p.minute_strategy_id" in routes_sql
    assert "s.minute_strategy_id=v.minute_strategy_id" in routes_sql


def test_same_route_event_is_idempotent_but_open_position_is_not_an_entry_gate():
    source=open("src/minute_ma/real_live.py",encoding="utf-8").read()
    assert "MINUTE_REAL_V1_5|ENTRY|{route.route_id}|{event_key}" in source
    assert "WHERE intent_key=%s" in source
    assert "OPEN_POSITION" not in source


def test_first_successful_route_poll_bootstraps_without_entry_replay():
    source=open("src/minute_ma/real_live.py",encoding="utf-8").read()
    assert "if route.cursor is None:" in source
    assert 'counts["BOOTSTRAPPED_NO_REPLAY"]+=1' in source
