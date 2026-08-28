"""Validate and summarize the official KRX_CONTINUOUS_AFTERNOON artifact."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path


def parser() -> argparse.ArgumentParser:
    result=argparse.ArgumentParser()
    result.add_argument("--historical",type=Path,required=True)
    result.add_argument("--paths",type=Path,required=True)
    result.add_argument("--output",type=Path,required=True)
    return result


def _read(path: Path):
    with path.open("r",encoding="utf-8-sig",newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    args=parser().parse_args();rows=_read(args.historical);paths=_read(args.paths)
    expected={row["source_daily_strategy_id"]:row for row in paths}
    actual_ids=[row["전략id"] for row in rows]
    duplicates=sorted(key for key,count in Counter(actual_ids).items() if count>1)
    missing=sorted(set(expected)-set(actual_ids));extra=sorted(set(actual_ids)-set(expected))
    invariant_errors=[]
    for row in rows:
        sid=row["전략id"];path=expected.get(sid)
        if path is None:continue
        if row["계산방식"]!="KRX_CONTINUOUS_AFTERNOON":invariant_errors.append([sid,"axis"])
        if int(row["정상청산수"])+int(row["마감1519청산수"])!=int(row["거래수"]):
            invariant_errors.append([sid,"exit_count"])
        if row["방향"]!=path["direction"]:invariant_errors.append([sid,"direction"])
        if row["실행상품코드"]!=path["execution_code"]:invariant_errors.append([sid,"execution"])
        if row["진입ma"]!=f'{path["entry_fast_ma"]}/{path["entry_slow_ma"]}':
            invariant_errors.append([sid,"entry_ma"])
        if row["청산ma"]!=f'{path["exit_fast_ma"]}/{path["exit_slow_ma"]}':
            invariant_errors.append([sid,"exit_ma"])
        expected_trend=path["trend_ma"] or "NONE"
        if row["추세ma"]!=expected_trend:invariant_errors.append([sid,"trend_ma"])
        profit=Decimal(row["누적복리손익"]);capital=Decimal(row["최종복리자본"])
        compound=Decimal(row["누적복리수익률_pct"])
        if capital-profit!=Decimal("1000000"):invariant_errors.append([sid,"capital_profit"])
        if abs(compound-profit/Decimal("10000"))>Decimal("0.0001"):
            invariant_errors.append([sid,"compound_profit"])
    selected=[row for row in rows if Decimal(row["누적복리수익률_pct"])>=Decimal("10.0")]
    selected.sort(key=lambda row:(-Decimal(row["누적복리수익률_pct"]),row["전략id"]))
    distribution=lambda key:dict(sorted(Counter(row[key] for row in selected).items()))
    summary={
      "historical_rows":len(rows),"unique_strategy_ids":len(set(actual_ids)),
      "selected":len(selected),"not_selected":len(rows)-len(selected),
      "compound_return_ge_10_candidates":len(selected),"threshold":"compound_return_pct >= 10.0",
      "selected_distribution":{
        "instrument_direction":dict(sorted(Counter(
          f'{row["신호종목"]}|{row["방향"]}' for row in selected).items())),
        "entry_ma":distribution("진입ma"),"exit_ma":distribution("청산ma"),
        "trend_ma":distribution("추세ma"),
      },
      "top_candidates":[{
        "strategy_id":row["전략id"],"instrument":row["신호종목"],
        "execution_code":row["실행상품코드"],"direction":row["방향"],
        "entry_ma":row["진입ma"],"exit_ma":row["청산ma"],"trend_ma":row["추세ma"],
        "trade_count":int(row["거래수"]),"win_rate_pct":row["승률_pct"],
        "compound_return_pct":row["누적복리수익률_pct"],
      } for row in selected[:20]],
      "validation":{
        "duplicate_strategy_ids":duplicates,"missing_strategy_ids":missing,
        "extra_strategy_ids":extra,"invariant_errors":invariant_errors,
        "status":"PASS" if len(rows)==2400 and len(set(actual_ids))==2400
                 and not duplicates and not missing and not extra and not invariant_errors else "FAIL",
      },
    }
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,sort_keys=True))
    return 0 if summary["validation"]["status"]=="PASS" else 1


if __name__=="__main__":raise SystemExit(main())
