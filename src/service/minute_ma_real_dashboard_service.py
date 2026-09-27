"""Read-only API query for the isolated Minute-MA REAL PAPER dashboard."""
from __future__ import annotations

from datetime import date

LEGACY9={"2282","2259","2286","2116","2284","2357","2046","2117","2382"}


def list_rows(pool, *, snapshot_date: date | None = None, filter_code: str | None = None,
              sort: str = "compound_cumulative_rank", limit: int = 100, offset: int = 0) -> dict:
    allowed_sort={f"{model}_{metric}" for model in ("compound","fixed")
                  for metric in ("cumulative_rank","recent20_rank","month_rank","week_rank",
                                 "day_rank","cumulative_return","cumulative_profit")}
    allowed_sort.add("compound_current_capital")
    if sort not in allowed_sort: sort="compound_cumulative_rank"
    limit=max(1,min(int(limit),500)); offset=max(0,int(offset))
    with pool.connection() as connection,connection.cursor() as cursor:
        if snapshot_date is None:
            cursor.execute("SELECT max(snapshot_date) FROM minute_ma_real_daily_snapshot")
            snapshot_date=cursor.fetchone()[0]
        if snapshot_date is None:
            return {"snapshot_date":None,"rows":[],"total":0}
        where="d.snapshot_date=%s"; params:list=[snapshot_date]
        if filter_code:
            where+=" AND d.filter_code=%s"; params.append(filter_code)
        cursor.execute(f"""SELECT count(*) FROM minute_ma_real_daily_snapshot d WHERE {where}""",params)
        total=int(cursor.fetchone()[0])
        direction="ASC" if sort.endswith("rank") else "DESC"
        cursor.execute(f"""SELECT d.*,COALESCE(c.inclusion_reasons,ARRAY[]::text[]) AS reasons,
          (c.candidate_state IN ('ENTERED','STAYED')) AS current_candidate
          FROM minute_ma_real_daily_snapshot d LEFT JOIN minute_ma_real_candidate_snapshot c
            ON c.snapshot_date=d.snapshot_date AND c.real_variant_id=d.real_variant_id
          WHERE {where} ORDER BY d.{sort} {direction},d.strategy_id LIMIT %s OFFSET %s""",
          params+[limit,offset])
        names=[column.name for column in cursor.description]
        rows=[dict(zip(names,row)) for row in cursor.fetchall()]
        for row in rows:
            row["legacy9_source"]=str(row["strategy_id"]) in LEGACY9
            # The P1 derived source was intentionally retired; no reconstruction
            # or inferred membership is performed by this new runtime.
            row["p1_source"]=False
    return {"snapshot_date":snapshot_date,"rows":rows,"total":total,
            "limit":limit,"offset":offset}
