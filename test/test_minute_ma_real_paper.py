from datetime import date,datetime
from decimal import Decimal

from src.minute_ma.real_paper import (BUY_FEE_RATE, CandidateTrade, RealFilter,
    RealSnapshot, daily_max_concurrency, eligible_entry_time, k_mode,
    passing_filters, replay_slots)
from src.minute_ma.real_paper_eod import candidate_reasons,rank_records


def snap(v=1,a3=1,a10=1,f5=1,f20=1,complete=True):
    return RealSnapshot(*(Decimal(str(x)) if x is not None else None
                          for x in (v,a3,a10,f5,f20)),complete)


def trade(key,entry,exit_,entry_price=100,exit_price=110):
    return CandidateTrade(key,entry.date(),entry,exit_,Decimal(entry_price),Decimal(exit_price))


def test_filter_contract_is_nested_and_null_fail_closed():
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


def test_k_mode_uses_daily_max_concurrency_exit_before_entry_and_carry_days():
    d1=date(2026,9,4); d2=date(2026,9,7); d3=date(2026,9,8)
    rows=[trade("a",datetime(2026,9,4,15,1),datetime(2026,9,8,9,5)),
          trade("b",datetime(2026,9,7,15,1),datetime(2026,9,7,15,5)),
          trade("c",datetime(2026,9,7,15,5),datetime(2026,9,8,9,6))]
    assert daily_max_concurrency(rows,market_dates=(d1,d2,d3))=={d1:1,d2:2,d3:2}
    assert k_mode(rows,market_dates=(d1,d2,d3))==2


def test_slot_replay_costs_integer_quantity_and_compounds():
    rows=[trade("a",datetime(2026,9,1,15,1),datetime(2026,9,2,9,5)),
          trade("b",datetime(2026,9,2,9,5),datetime(2026,9,2,10,0),100,90)]
    result=replay_slots(rows,slot_count=1,initial_capital=Decimal("1000"))
    assert [row.status for row in result]==["CLOSED","CLOSED"]
    assert result[0].quantity==int(Decimal(1000)/(Decimal(100)*(1+BUY_FEE_RATE)))
    assert result[1].capital_before==result[0].capital_after
    assert result[0].buy_fee>0 and result[0].sell_fee>0 and result[0].sell_tax>0
    assert result[0].realized_pnl==result[0].gross_pnl-result[0].buy_fee-result[0].sell_fee-result[0].sell_tax


def test_slot_limit_records_no_slot_without_double_using_capital():
    rows=[trade("a",datetime(2026,9,1,15,1),datetime(2026,9,2,9,5)),
          trade("b",datetime(2026,9,1,15,2),datetime(2026,9,2,9,6))]
    result=replay_slots(rows,slot_count=1)
    assert [row.status for row in result]==["CLOSED","SKIPPED_NO_SLOT"]


def test_open_candidate_preserves_exit_responsibility_and_blocks_slot():
    rows=[trade("a",datetime(2026,9,1,15,1),None),
          trade("b",datetime(2026,9,2,15,1),datetime(2026,9,3,9,5))]
    result=replay_slots(rows,slot_count=1)
    assert result[0].status=="OPEN" and result[0].capital_after is None
    assert result[1].status=="SKIPPED_NO_SLOT"


def test_only_normal_exit_is_representable_in_migration():
    sql=open("database/migrations/20260927_minute_ma_real_paper_additive.sql",encoding="utf-8").read()
    assert "exit_reason='NORMAL_EXIT'" in sql
    assert "STOP_EXIT" not in sql
    assert "EOD_1519" not in sql


def test_runtime_has_no_live_or_broker_dependency():
    source=open("src/minute_ma/real_paper_runtime.py",encoding="utf-8").read()
    assert "live_transport" not in source
    assert "kis_order_transport" not in source
    assert "live_repository" not in source
    assert "close_eod" not in source


def test_ranks_and_candidate_union_are_filter_independent_but_strategy_based():
    records=[]
    variant=0
    for filter_code in ("REAL_F1","REAL_F2","REAL_F3"):
        for index in range(101):
            variant+=1
            profit=Decimal(101-index)
            records.append({"variant":variant,"strategy":f"S{index:03d}",
              "filter":filter_code,"initial":Decimal(1000),
              "profits":{period:profit for period in ("cumulative","recent20","month","week","day")}})
    ranks=rank_records(records)
    by_strategy,by_variant=candidate_reasons(records,ranks)
    assert len(by_strategy)==100
    assert "S100" not in by_strategy
    s0_variants=[row["variant"] for row in records if row["strategy"]=="S000"]
    assert all(len(by_variant[variant])==9 for variant in s0_variants)
