"""Date-loop only: reuse the existing --date compute/publish runner unchanged.

No source DML, migration, overwrite or duplicate replay engine. Defaults to plan.
"""
import argparse
from datetime import date, datetime, time, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import psycopg
from src.flow_v3_leadership.research import VERSION, PERIODS
from src.flow_v3_leadership.source import capitals

TOP5 = ('FV3008049', 'FV3008051', 'FV3008209', 'FV3008977', 'FV3008084')


def plan(conn, through):
    first = conn.execute("""SELECT min(t.entry_signal_time)::date
        FROM flow_v3_paper_trade t JOIN flow_v3_strategy_master m USING(strategy_id)
        WHERE m.direction='LONG' AND m.is_enabled='Y' AND m.stock_code IN ('000660','005930')""").fetchone()[0]
    if first is None or first > through:
        raise ValueError('NO_RESEARCH_HISTORY_IN_RANGE')
    days = [r[0] for r in conn.execute("""SELECT DISTINCT bar_time::date
        FROM raw_stock_minute WHERE stock_code IN ('000660','005930')
        AND data_source='KIS' AND trading_venue='KRX' AND collect_cycle='1MIN'
        AND bar_time >= %s AND bar_time < %s ORDER BY 1""",
        (datetime.combine(first, time()), datetime.combine(through+timedelta(days=1), time()))).fetchall()]
    blockers = []
    if not days or days[0] != first:
        blockers.append('FIRST_HOLD_HISTORY_DATE_HAS_NO_PRICE_SOURCE')
    relations = conn.execute("""SELECT to_regclass('flow_v3_leadership_run'),
        to_regclass('flow_v3_leadership_snapshot')""").fetchone()
    if not all(relations):
        blockers.append('LEADERSHIP_SCHEMA_NOT_INSTALLED')
    try:
        axes = capitals(conn)
    except ValueError as error:
        axes = []
        blockers.append(str(error))
    if all(relations):
        old = conn.execute("""SELECT snapshot_date,version FROM flow_v3_leadership_run
            WHERE snapshot_date >= %s AND snapshot_date <= %s AND version <> %s
            ORDER BY snapshot_date""", (first, through, VERSION)).fetchall()
        if old:
            blockers.append('IMMUTABLE_OLD_COST_VERSION_CONFLICT:' + str(old))
    return dict(research_start=first, through=through, dates=days, version=VERSION,
                capital_axes=axes, blockers=blockers)


def execute_plan(work, *, write=False, runner=subprocess.run):
    if work['blockers']:
        raise ValueError('BACKFILL_BLOCKED:' + '|'.join(work['blockers']))
    for day in work['dates']:
        command = [sys.executable, str(ROOT/'scripts/research/run_flow_v3_leadership.py'),
                   '--date', str(day), '--research-start', str(work['research_start'])]
        if write:
            command.append('--write-snapshot')
        # Existing runner owns asof/time validation, read-only compute and safe writer checks.
        # Stop on first error; committed earlier dates remain immutable/re-runnable.
        runner(command, check=True, cwd=ROOT)


def ranking_report(conn, through):
    latest = conn.execute("""SELECT max(snapshot_date) FROM flow_v3_leadership_run
        WHERE snapshot_date<=%s AND version=%s""", (through, VERSION)).fetchone()[0]
    if latest is None:
        raise ValueError('NO_TAX_INCLUDED_SNAPSHOT')
    base = next(a['amount'] for a in capitals(conn) if a['is_default'])
    result = dict(snapshot_date=latest, capital_base=base, version=VERSION, periods={})
    for period in PERIODS:
        key = 'regular_' + period
        select = f"""SELECT strategy_id,stock_code,{key}->>'status' AS status,
            ({key}->>'rank')::integer AS rank,
            ({key}->>'compound_return')::numeric AS compound_return,
            ({key}->>'final_capital')::numeric AS final_capital,
            ({key}->>'net_profit')::numeric AS net_profit,
            ({key}->>'trade_count')::integer AS trade_count,
            ({key}->>'mdd')::numeric AS mdd,
            ({key}->>'normal_exit_count')::integer AS normal_exit_count,
            ({key}->>'eod_exit_count')::integer AS eod_exit_count,
            ({key}->>'overnight_count')::integer AS overnight_count
            FROM flow_v3_leadership_snapshot WHERE snapshot_date=%s AND capital_base=%s"""

        def read(query, params):
            cur = conn.execute(query, params)
            names = [c.name for c in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]

        top = read(select + f" AND {key}->>'status'='COMPLETE' AND {key}->>'rank' IS NOT NULL"
                   f" ORDER BY ({key}->>'rank')::integer,strategy_id LIMIT 50", (latest, base))
        candidates = read(select + ' AND strategy_id=ANY(%s) ORDER BY strategy_id',
                          (latest, base, list(TOP5)))
        for row in candidates:
            row['in_top50'] = row['rank'] is not None and row['rank'] <= 50
        result['periods'][period] = dict(top50=top, live_top5=candidates)
    result['unavailable'] = conn.execute("""SELECT snapshot_date,j.key,j.value->>'status',count(*)
        FROM flow_v3_leadership_snapshot s JOIN flow_v3_leadership_run r USING(snapshot_date)
        CROSS JOIN LATERAL jsonb_each(jsonb_build_object(
          'EXTENDED_EXIT',s.extended_exit_daily,'EXTENDED_FULL',s.extended_full_daily,'AFTER',s.after_daily)) j
        WHERE snapshot_date<=%s AND capital_base=%s AND r.version=%s AND j.value->>'status'<>'COMPLETE'
        GROUP BY snapshot_date,j.key,j.value->>'status' ORDER BY 1,2,3""", (latest, base, VERSION)).fetchall()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--through', type=date.fromisoformat, required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--compute', action='store_true', help='date-loop without publication')
    action.add_argument('--write-snapshot', action='store_true')
    action.add_argument('--report-only', action='store_true')
    args = parser.parse_args()
    read = os.environ.get('LEADERSHIP_READ_DSN')
    if not read:
        parser.error('LEADERSHIP_READ_DSN required')
    with psycopg.connect(read) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conn.execute("SET LOCAL statement_timeout='120s'")
        work = ranking_report(conn, args.through) if args.report_only else plan(conn, args.through)
    print(json.dumps(work, default=str, ensure_ascii=False), flush=True)
    if not args.report_only and (args.compute or args.write_snapshot):
        execute_plan(work, write=args.write_snapshot)
    return 2 if work.get('blockers') else 0


if __name__ == '__main__':
    raise SystemExit(main())
