from datetime import datetime
from decimal import Decimal

from src.minute_ma.real_paper import (
    BUY_FEE_RATE, CandidateTrade, RealFilter, RealSnapshot, eligible_entry_time,
    passing_filters, replay_parallel_capital,
)
from src.minute_ma.real_paper_eod import candidate_reasons, rank_records


def snap(v=1,a3=1,a10=1,f5=1,f20=1,complete=True):
    return RealSnapshot(*(Decimal(str(x)) if x is not None else None
                          for x in (v,a3,a10,f5,f20)),complete)


def trade(key,entry,exit_,entry_price=100,exit_price=110):
    return CandidateTrade(key,entry.date(),entry,exit_,Decimal(entry_price),
                          Decimal(exit_price) if exit_ else None)


def test_filter_contract_is_nested_and_null_fail_closed():
    assert tuple(RealFilter)==(RealFilter.BASE,RealFilter.F1,RealFilter.F2,RealFilter.F3)
    assert passing_filters(snap())==(RealFilter.F1,RealFilter.F2,RealFilter.F3)
    assert passing_filters(snap(f5=-1))==(RealFilter.F1,RealFilter.F2)
    assert passing_filters(snap(a3=-1))==(RealFilter.F1,)
    assert passing_filters(snap(v=None))==()
    assert passing_filters(snap(complete=False))==()


def test_entry_window_is_exactly_1500_through_1518():
    assert not eligible_entry_time(datetime(2026,9,1,14,59))
    assert eligible_entry_time(datetime(2026,9,1,15,0))
    assert eligible_entry_time(datetime(2026,9,1,15,18,59))
    assert not eligible_entry_time(datetime(2026,9,1,15,19))


def test_overlapping_entries_are_not_slot_gated_and_share_settled_capital():
    rows=[trade("a",datetime(2026,9,1,15,1),datetime(2026,9,2,9,5)),
          trade("b",datetime(2026,9,1,15,2),datetime(2026,9,2,9,6))]
    result=replay_parallel_capital(rows,initial_capital=Decimal("1000"))
    assert [row.status for row in result.trades]==["CLOSED","CLOSED"]
    assert result.trades[0].entry_realized_capital==Decimal("1000")
    assert result.trades[1].entry_realized_capital==Decimal("1000")
    assert all(row.compound.quantity>0 for row in result.trades)


def test_exit_precedes_entry_at_same_timestamp_and_fixed_quantity_stays_10m_based():
    rows=[trade("a",datetime(2026,9,1,15,1),datetime(2026,9,2,15,1)),
          trade("b",datetime(2026,9,2,15,1),datetime(2026,9,3,9,6),100,90)]
    result=replay_parallel_capital(rows,initial_capital=Decimal("1000"))
    first,second=result.trades
    assert second.entry_realized_capital==first.settlement_realized_capital_after
    assert second.compound.quantity==int(second.entry_realized_capital/
        (Decimal(100)*(1+BUY_FEE_RATE)))
    assert second.fixed.quantity==int(Decimal(1000)/
        (Decimal(100)*(1+BUY_FEE_RATE)))


def test_open_trade_does_not_change_realized_capital_or_block_next_entry():
    rows=[trade("a",datetime(2026,9,1,15,1),None),
          trade("b",datetime(2026,9,2,15,1),datetime(2026,9,3,9,5))]
    result=replay_parallel_capital(rows,initial_capital=Decimal("1000"))
    assert result.trades[0].status=="OPEN"
    assert result.trades[1].entry_realized_capital==Decimal("1000")


def test_costs_include_buy_sell_fee_and_sell_tax():
    row=replay_parallel_capital([
        trade("a",datetime(2026,9,1,15,1),datetime(2026,9,2,9,5))
    ],initial_capital=Decimal("1000")).trades[0]
    assert row.compound.buy_fee>0 and row.compound.sell_fee>0 and row.compound.sell_tax>0
    assert row.compound.realized_pnl==(row.compound.gross_pnl-row.compound.buy_fee-
                                      row.compound.sell_fee-row.compound.sell_tax)


def test_followup_migration_removes_slot_gate_and_keeps_normal_exit_only():
    sql=open("database/migrations/20260927_minute_ma_real_paper_v13.sql",encoding="utf-8").read()
    assert "DROP TABLE minute_ma_real_paper_slot" in sql
    assert "minute_ma_real_capital_epoch" in sql
    assert "SKIPPED_NO_SLOT" not in sql
    assert "NORMAL_EXIT" in sql


def test_v15_migration_is_additive_base_and_exact_live_route_contract():
    sql=open("database/migrations/20260928_minute_ma_real_base_live_routes.sql",encoding="utf-8").read()
    assert "('BASE','REAL_F1','REAL_F2','REAL_F3')" in sql
    assert "V1_5_TOP50_FIXED_ONE" in sql
    assert "V1_5_UNDERLYING_INITIAL" in sql
    assert sql.count("'V1_5_TOP50_FIXED_ONE'")==1
    assert "('2161','REAL_F1',2000000::numeric)" in sql
    assert "('2163','REAL_F1',0::numeric)" in sql
    assert "('2186','REAL_F2',0::numeric)" in sql
    assert "('1934','REAL_F1',0::numeric)" in sql
    assert "('2185','REAL_F2',0::numeric)" in sql
    assert "DELETE FROM minute_ma_real" not in sql


def test_runtime_has_no_live_or_broker_dependency():
    source=open("src/minute_ma/real_paper_runtime.py",encoding="utf-8").read()
    assert "live_transport" not in source
    assert "kis_order_transport" not in source
    assert "live_repository" not in source
    assert "close_eod" not in source


def test_eod_skips_dates_without_real_market_bars():
    source=open("src/minute_ma/real_paper_eod.py",encoding="utf-8").read()
    assert "SELECT EXISTS(SELECT 1 FROM raw_stock_minute" in source
    assert "if not cursor.fetchone()[0]" in source


def test_ranks_and_candidate_union_use_compound_only_and_strategy_identity():
    records=[]; variant=0
    for filter_code in ("BASE","REAL_F1","REAL_F2","REAL_F3"):
        for index in range(101):
            variant+=1; profit=Decimal(101-index)
            records.append({"variant":variant,"strategy":f"S{index:03d}",
              "filter":filter_code,"initial":Decimal(1000),
              "compound_profits":{p:profit for p in ("cumulative","recent20","month","week","day")},
              "fixed_profits":{p:-profit for p in ("cumulative","recent20","month","week","day")}})
    ranks=rank_records(records,"compound")
    by_strategy,by_variant=candidate_reasons(records,ranks)
    assert len(by_strategy)==100 and "S100" not in by_strategy
    s0=[row["variant"] for row in records if row["strategy"]=="S000"]
    assert all(len(by_variant[v])==12 for v in s0)
