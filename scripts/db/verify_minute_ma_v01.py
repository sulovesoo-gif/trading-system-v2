"""Machine-readable verification for the additive minute-MA layer."""
from __future__ import annotations

import json
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings,create_connection_pool


def main() -> int:
    load_dotenv()
    pool=create_connection_pool(DatabaseSettings.from_environment())
    checks={
      "minute_strategy_count":"SELECT count(*) FROM minute_ma_strategy_master WHERE is_enabled='Y'",
      "minute_path_count":"SELECT count(*) FROM minute_ma_path WHERE is_enabled='Y'",
      "current_operation_count":"SELECT count(*) FROM minute_ma_operation WHERE effective_to IS NULL",
      "paper_capital_count":"SELECT count(*) FROM minute_ma_paper_capital",
      "dashboard_count":"SELECT count(*) FROM vw_minute_ma_dashboard",
      "research_802_count":"SELECT count(*) FROM research_strategy_master",
      "daily_master_count":"SELECT count(*) FROM daily_strategy_master",
      "daily_paper_count":"SELECT count(*) FROM daily_strategy_paper_trade",
      "daily_live_count":"SELECT count(*) FROM daily_strategy_live_trade",
      "send_enabled":"SELECT send_enabled FROM minute_ma_send_profile WHERE profile_code='MINUTE_MA_LIVE_SEND'",
      "capital_invariant_errors":"SELECT count(*) FROM minute_ma_paper_capital WHERE current_capital<>initial_capital+cumulative_realized_pnl",
      "duplicate_events":"SELECT count(*) FROM (SELECT 1 FROM minute_ma_paper_event GROUP BY minute_path_id,signal_event_key,event_type HAVING count(*)>1)d",
      "duplicate_trades":"SELECT count(*) FROM (SELECT 1 FROM minute_ma_paper_trade GROUP BY minute_path_id,entry_event_key HAVING count(*)>1)d",
      "orphan_paths":"SELECT count(*) FROM minute_ma_path p LEFT JOIN minute_ma_strategy_master s USING(minute_strategy_id) WHERE s.minute_strategy_id IS NULL",
      "orphan_links":"SELECT count(*) FROM minute_ma_live_order_link l LEFT JOIN live_order_request r ON r.order_request_id=l.order_request_id WHERE r.order_request_id IS NULL",
      "live_settlement_count":"SELECT count(*) FROM minute_ma_live_capital_settlement",
    }
    result={}
    try:
      with pool.connection() as connection,connection.cursor() as cursor:
        for name,sql in checks.items():
          cursor.execute(sql);result[name]=cursor.fetchone()[0]
        cursor.execute("SELECT data_axis,count(*) FROM minute_ma_path GROUP BY data_axis ORDER BY data_axis")
        result["paths_by_axis"]={str(axis):count for axis,count in cursor.fetchall()}
        cursor.execute("""SELECT signal_code,direction,execution_code,count(*)
                            FROM minute_ma_strategy_master GROUP BY 1,2,3 ORDER BY 1,2""")
        result["semantics_by_instrument"]=[{"signal_code":str(s),"direction":str(d),
          "execution_code":str(e),"count":int(c)} for s,d,e,c in cursor.fetchall()]
      expected={"minute_strategy_count":2400,"minute_path_count":9600,"current_operation_count":9600,
                "paper_capital_count":9600,"dashboard_count":9600,"research_802_count":802,
                "send_enabled":"N","capital_invariant_errors":0,"duplicate_events":0,
                "duplicate_trades":0,"orphan_paths":0,"orphan_links":0}
      errors={key:{"expected":value,"actual":result.get(key)} for key,value in expected.items()
              if result.get(key)!=value}
      if set(result["paths_by_axis"].values())!={2400} or len(result["paths_by_axis"])!=4:
          errors["paths_by_axis"]={"expected":"4 x 2400","actual":result["paths_by_axis"]}
      if len(result["semantics_by_instrument"])!=4 or any(row["count"]!=600 for row in result["semantics_by_instrument"]):
          errors["semantics_by_instrument"]={"expected":"4 x 600","actual":result["semantics_by_instrument"]}
      result["status"]="PASS" if not errors else "FAIL";result["errors"]=errors
      print(json.dumps(result,ensure_ascii=False,sort_keys=True,default=str))
      return 0 if not errors else 1
    finally: pool.close()


if __name__=="__main__": raise SystemExit(main())
