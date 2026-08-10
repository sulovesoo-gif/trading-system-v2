"""Admin-only RAW backfill and COMPLETE research orchestration.

The service is intentionally not imported by any realtime runner.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4

from src.analysis.feature.sma_feature import MinuteBar
from src.repository.raw_specs import RawTable
from src.service.research_complete_replay_service import CompleteReplay, STRATEGIES, _session_id


@dataclass(frozen=True)
class ResearchCostPolicy:
    """Run-snapshotted costs; environment overrides preserve account-specific rates."""
    version: str = "KIS_BanKIS_2026_08"
    stock_fee_rate: Decimal = Decimal("0.000140527")
    etf_etn_fee_rate: Decimal = Decimal("0.000146527")
    stock_sell_tax_rate: Decimal = Decimal("0")
    etf_etn_sell_tax_rate: Decimal = Decimal("0")
    slippage_rate: Decimal = Decimal("0")

    def for_stock(self, stock_code: str) -> tuple[Decimal, Decimal]:
        # The official research universe contains one common share and ETF
        # products.  New instruments must choose a policy explicitly.
        return ((self.stock_fee_rate, self.stock_sell_tax_rate)
                if stock_code == "000660" else (self.etf_etn_fee_rate, self.etf_etn_sell_tax_rate))

    def snapshot(self) -> dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}


@dataclass(frozen=True)
class ResearchPair:
    name: str
    trade_stock_code: str
    signal_source_stock_code: str
    transform: str = "DIRECT"


OFFICIAL_PAIRS = (
    ResearchPair("underlying_from_underlying", "000660", "000660"),
    ResearchPair("underlying_from_leverage", "000660", "0193T0"),
    ResearchPair("underlying_from_inverse", "000660", "0197X0", "INVERT"),
    ResearchPair("leverage_from_underlying", "0193T0", "000660"),
    ResearchPair("leverage_from_inverse", "0193T0", "0197X0", "INVERT"),
    ResearchPair("leverage_from_leverage", "0193T0", "0193T0"),
    ResearchPair("inverse_from_underlying", "0197X0", "000660", "INVERT"),
    ResearchPair("inverse_from_leverage", "0197X0", "0193T0", "INVERT"),
    ResearchPair("inverse_from_inverse", "0197X0", "0197X0"),
    # Consensus requires both sources and is deliberately retained as a named
    # configuration for the next extension; this first COMPLETE implementation
    # never invents a one-source substitute.
    ResearchPair("leverage_consensus_underlying_inverse", "0193T0", "000660+0197X0", "CONSENSUS"),
)


class ResearchBackfillService:
    def __init__(self, *, minute_collector, daily_collector, raw_ingestion, calendar, minute_backfill=None, daily_backfill=None):
        self.minute_collector = minute_collector
        self.daily_collector = daily_collector
        self.raw_ingestion = raw_ingestion
        self.calendar = calendar
        self.minute_backfill = minute_backfill
        self.daily_backfill = daily_backfill

    def backfill_daily(self, *, stock_code: str, start_date: date, end_date: date, venue: str = "INTEGRATED"):
        if venue not in {"INTEGRATED", "KRX"}:
            raise ValueError("daily backfill venue must be INTEGRATED or KRX")
        rows = self.daily_collector.collect(stock_code=stock_code, market_code="KOSPI", trading_venue=venue,
                                            start_date=start_date.strftime("%Y%m%d"), end_date=end_date.strftime("%Y%m%d"))
        rows = [row for row in rows if start_date <= row["trade_date"] <= end_date]
        result = self.raw_ingestion.store(RawTable.STOCK_DAILY, rows)
        if venue == "KRX":
            # This is intentionally after idempotent RAW storage.  It never
            # consults INTEGRATED daily prices or overwrites a KIS-supplied
            # minute previous_close_price.
            self.raw_ingestion.populate_minute_previous_close_from_krx_daily(stock_code=stock_code)
        return result

    def backfill_minutes(self, *, stock_code: str, start_date: date, end_date: date, venue: str = "INTEGRATED") -> list[dict]:
        result: list[dict] = []
        open_dates = set(self.calendar.open_dates(start_date, end_date))
        day = start_date
        while day <= end_date:
            if day not in open_dates:
                result.append({"date": day.isoformat(), "status": "SKIPPED_NON_TRADING", "inserted": 0, "existing": 0})
                day += timedelta(days=1); continue
            try:
                cursor = datetime.combine(day, time(23, 59, 59))
                rows: list[dict] = []
                # KIS returns a bounded reverse-time page. Continue from one
                # minute before the oldest row; never infer missing minutes.
                for _page in range(20):
                    page = self.minute_collector.collect(stock_code=stock_code, market_code="KOSPI", trading_venue=venue,
                                                         input_date=cursor.strftime("%Y%m%d"), input_hour=cursor.strftime("%H%M%S"), previous_data_include_yn="N")
                    same_day = [row for row in page if row["bar_time"].date() == day]
                    rows.extend(same_day)
                    if len(page) < 120 or not page:
                        break
                    cursor = min(row["bar_time"] for row in page) - timedelta(minutes=1)
                    if cursor.date() != day:
                        break
                rows = list({row["bar_time"]: row for row in rows}.values())
                saved = self.raw_ingestion.store(RawTable.STOCK_MINUTE, rows)
                result.append({"date": day.isoformat(), "status": "SUCCESS", "requested": saved.requested_count,
                               "inserted": saved.inserted_count, "existing": saved.duplicate_count})
            except Exception as error:  # Continue one-date failure as requested.
                result.append({"date": day.isoformat(), "status": "FAILED", "error": f"{type(error).__name__}: {error}", "inserted": 0, "existing": 0})
            day += timedelta(days=1)
        return result


class CompleteResearchRunner:
    def __init__(self, *, pool, repository) -> None:
        self.pool, self.repository = pool, repository

    def _bars(self, stock_code: str, start_date: date, end_date: date) -> list[MinuteBar]:
        venue = "INTEGRATED" if stock_code in {"000660", "005930"} else "KRX"
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute("""SELECT bar_time,open_price,high_price,low_price,close_price FROM raw_stock_minute
            WHERE stock_code=%s AND data_source='KIS' AND market_code='KOSPI' AND trading_venue=%s
              AND collect_cycle='1MIN' AND bar_time::date BETWEEN %s AND %s ORDER BY bar_time""", (stock_code,venue,start_date,end_date))
            return [MinuteBar(*row) for row in cur.fetchall()]

    def run(self, *, start_date: date, end_date: date, pairs=OFFICIAL_PAIRS,
            entry_condition: str = CompleteReplay.MA10_CONFIRM,
            cost_policy: ResearchCostPolicy | None = None):
        if entry_condition not in {CompleteReplay.SIGNAL_ONLY, CompleteReplay.MA10_CONFIRM}:
            raise ValueError(f"unsupported entry_condition: {entry_condition}")
        cost_policy = cost_policy or ResearchCostPolicy()
        run_id = uuid4()
        self.repository.create_run(run_id=run_id, start_date=start_date, end_date=end_date,
                                   parameters={"observation": "COMPLETE", "capital": "10000000", "entry_condition": entry_condition,
                                               "cost_policy_version": cost_policy.version, "cost_policy": cost_policy.snapshot(),
                                               "fee_rate": str(cost_policy.stock_fee_rate), "slippage_rate": str(cost_policy.slippage_rate),
                                               "pairs": [pair.name for pair in pairs]})
        replay = CompleteReplay(entry_condition=entry_condition)
        try:
            all_bars = {code: self._bars(code, start_date, end_date) for code in {part for pair in pairs for part in pair.signal_source_stock_code.split("+")} | {pair.trade_stock_code for pair in pairs}}
            for source, bars in all_bars.items():
                previous = None
                for feature in replay.features(bars):
                    direction = replay._ma10_direction(previous, feature)
                    self.repository.save_feature(run_id=run_id, stock_code=source, feature=feature, ma10_direction=direction)
                    previous = feature
            for pair in pairs:
                if pair.transform == "CONSENSUS":
                    left, right = pair.signal_source_stock_code.split("+")
                    source_features = replay.features(all_bars[left])
                    right_signals = replay.canonical_signals(replay.features(all_bars[right]))
                    right_by_key = {(event.at, event.signal_type, event.direction) for event in right_signals}
                    # Official agreement: underlying LONG + inverse SHORT opens
                    # leverage LONG, and the exact opposite opens leverage SHORT.
                    signals = []
                    for event in replay.canonical_signals(source_features):
                        expected = "SHORT" if event.direction == "LONG" else "LONG"
                        if (event.at, event.signal_type, expected) in right_by_key:
                            signals.append(event)
                else:
                    source_features = replay.features(all_bars[pair.signal_source_stock_code])
                    signals = replay.canonical_signals(source_features)
                direction_by_time = {feature.bar.bar_time: replay._ma10_direction(previous, feature) for previous, feature in zip([None, *source_features], source_features)}
                target_prices = {bar.bar_time: bar.close_price for bar in all_bars[pair.trade_stock_code]}
                fee_rate, sell_tax_rate = cost_policy.for_stock(pair.trade_stock_code)
                pair_replay = CompleteReplay(entry_condition=entry_condition, fee_rate=fee_rate,
                                              sell_tax_rate=sell_tax_rate, slippage_rate=cost_policy.slippage_rate)
                cycles = pair_replay.replay(features=source_features, signals=signals, target_prices=target_prices,
                                       direction_transform="DIRECT" if pair.transform == "CONSENSUS" else pair.transform)
                for strategy in STRATEGIES:
                    for signal in signals:
                        if signal.signal_type in ( {strategy} if strategy.startswith("SIGNAL_") else ({"SIGNAL_1","SIGNAL_2","SIGNAL_3"} if strategy=="ACCUMULATED" else ({"SIGNAL_1","SIGNAL_2"} if strategy=="ACCUMULATED_1" else {"SIGNAL_2","SIGNAL_3"})) ):
                            ma10_direction = direction_by_time.get(signal.at)
                            pending = entry_condition == CompleteReplay.MA10_CONFIRM and ma10_direction != signal.direction
                            confirm_time = signal.at if entry_condition == CompleteReplay.SIGNAL_ONLY or not pending else None
                            self.repository.save_signal(run_id=run_id, stock_code=pair.signal_source_stock_code, strategy_code=strategy, signal=signal, ma10_direction=ma10_direction, pending=pending, confirm_time=confirm_time, session_code=_session_id(signal.at))
                    for cycle in (item for item in cycles if item.strategy_code == strategy):
                        cycle_id = self.repository.save_cycle(run_id=run_id, trade_stock_code=pair.trade_stock_code, signal_source_stock_code=pair.signal_source_stock_code, cycle=cycle)
                        for leg in cycle.legs: self.repository.save_leg(cycle_id=cycle_id, leg=leg)
            self.repository.rebuild_performance(run_id=run_id, start_date=start_date, end_date=end_date)
            self.repository.finish_run(run_id, "COMPLETED")
            return run_id
        except Exception:
            self.repository.finish_run(run_id, "FAILED")
            raise
