"""Pure chronological feature/event engine for VIDEO_STRATEGY V1.

The engine never reads a database and never sees a future bar while deciding
the current state.  Future bars are accepted only by ``measure_event`` after
an event has already been emitted.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Iterable, Mapping

from src.analysis.feature.sma_feature import MinuteBar

D = Decimal
ZERO = D("0")


@dataclass(frozen=True)
class VideoParameters:
    strategy_family: str = "VIDEO_STRATEGY"
    strategy_version: str = "V1"
    signal_source_stock_code: str = "000660"
    execution_stock_codes: tuple[str, ...] = ("000660", "0193T0", "0197X0")
    timeframe: str = "1MIN"
    sma_length: int = 20
    sma_slope_window: int = 3
    sma_slope_min_ratio: D = D("0.0005")
    pivot_method: str = "PIVOT_FRACTAL_2"
    pivot_parameter: D = D("2")
    pullback_distance_ratio: D = D("0.003")
    pullback_allow_below_ratio: D = D("0.002")
    reclaim_max_bars: int = 5
    reclaim_confirm_method: str = "CLOSE"
    body_above_ratio: D = D("0.50")
    body_expansion_method: str = "BODY_EXP_PREVIOUS"
    body_expansion_window: int = 5
    body_expansion_ratio: D = D("1.20")
    body_expansion_percentile: D = D("0.75")
    volume_avg_method: str = "RVOL_SIMPLE"
    volume_avg_window: int = 20
    volume_spike_ratio: D = D("2.0")
    volume_drop_ratio: D = D("0.5")
    divergence_method: str = "DIVERGENCE_PIVOT"
    structure_break_method: str = "CLOSE"
    stop_method: str = "STOP_CLOSE"
    stop_distance_ratio: D = D("0.01")
    battle_wick_body_ratio: D = D("1.0")
    battle_repeat_bars: int = 3
    capital_policy: Mapping[str, str] = field(default_factory=lambda: {"type": "PARAMETER", "initial_capital": "10000000"})
    cost_policy_version: str = "CURRENT_RESEARCH"
    fee_rate: D = ZERO
    sell_tax_rate: D = ZERO
    slippage_rate: D = ZERO
    ablation: str = "FULL"
    variant: str = "VIDEO_BASE"

    def __post_init__(self) -> None:
        if self.strategy_family != "VIDEO_STRATEGY" or self.strategy_version != "V1" or self.timeframe != "1MIN":
            raise ValueError("VIDEO_STRATEGY V1 supports only its fixed family/version and 1MIN RAW")
        if self.sma_length != 20:
            raise ValueError("MA_LENGTH is FIXED at 20")
        if self.pivot_method not in {"PIVOT_FRACTAL_2", "PIVOT_FRACTAL_3", "PIVOT_FRACTAL_5", "PIVOT_ZIGZAG"}:
            raise ValueError("unsupported pivot_method")
        if self.ablation not in {"FULL", "NO_STRUCTURE", "NO_BODY", "NO_VOLUME", "NO_WICK"}:
            raise ValueError("unsupported ablation")
        if self.variant not in {"VIDEO_BASE", "VIDEO_PROGRAM", "VIDEO_EXECUTION_STRENGTH", "VIDEO_PROGRAM_EXECUTION_STRENGTH", "VIDEO_WICK_VOLUME_EXPERIMENTAL"}:
            raise ValueError("unsupported variant")
        for value in (self.sma_slope_window, self.reclaim_max_bars, self.body_expansion_window, self.volume_avg_window):
            if value <= 0:
                raise ValueError("windows must be positive")

    def snapshot(self) -> dict:
        result = asdict(self)
        result["execution_stock_codes"] = list(self.execution_stock_codes)
        return _jsonable(result)


@dataclass(frozen=True)
class Pivot:
    pivot_time: datetime
    pivot_price: D
    confirmed_time: datetime
    pivot_type: str
    pivot_method: str
    pivot_parameter: D


@dataclass(frozen=True)
class VideoFeature:
    bar: MinuteBar
    volume: D
    sma20: D | None
    sma20_slope: D | None
    sma20_direction: str | None
    body_size: D
    body_top: D
    body_bottom: D
    body_above_ratio: D | None
    body_below_ratio: D | None
    upper_wick: D
    lower_wick: D
    upper_wick_body_ratio: D | None
    lower_wick_body_ratio: D | None
    total_wick_body_ratio: D | None
    range_body_ratio: D | None
    body_expansion: bool
    volume_avg: D | None
    volume_ratio: D | None
    volume_change_ratio: D | None
    volume_slope: D | None
    pivot_high: Pivot | None
    pivot_low: Pivot | None
    market_state: str
    data_status: str

    def detail(self) -> dict:
        result = asdict(self)
        result.pop("bar")
        return _jsonable(result)


@dataclass(frozen=True)
class VideoEvent:
    at: datetime
    event_type: str
    direction: str
    price: D
    feature: VideoFeature
    detail: Mapping[str, object] = field(default_factory=dict)


def _jsonable(value):
    if isinstance(value, dict): return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)): return [_jsonable(item) for item in value]
    if isinstance(value, (D, datetime)): return str(value) if isinstance(value, D) else value.isoformat()
    return value


def _ratio(numerator: D, denominator: D) -> D | None:
    return None if denominator == 0 else numerator / denominator


def _session(value: datetime) -> str | None:
    hm = (value.hour, value.minute)
    if (8, 0) <= hm <= (8, 49): return "NXT_PREMARKET"
    if (9, 0) <= hm <= (15, 19): return "KRX_REGULAR"
    if (15, 40) <= hm <= (20, 0): return "NXT_AFTERMARKET"
    return None


def _contiguous(previous: MinuteBar | None, current: MinuteBar) -> bool:
    return (previous is not None and previous.bar_time.date() == current.bar_time.date()
            and _session(previous.bar_time) == _session(current.bar_time)
            and current.bar_time - previous.bar_time == timedelta(minutes=1))


class VideoFeatureEngine:
    """One-pass feature/state machine; pivots become visible only at confirmation."""
    def __init__(self, parameters: VideoParameters) -> None:
        self.p = parameters

    def build(self, rows: Iterable[tuple[MinuteBar, D]]) -> tuple[list[VideoFeature], list[VideoEvent]]:
        ordered = sorted(rows, key=lambda item: item[0].bar_time)
        features: list[VideoFeature] = []
        events: list[VideoEvent] = []
        group: list[tuple[MinuteBar, D]] = []
        highs: list[Pivot] = []
        lows: list[Pivot] = []
        pullback: dict[str, datetime] = {}
        warning_times: list[datetime] = []
        prior_state = "RANGE"
        active_direction: str | None = None
        active_entry_price: D | None = None
        for bar, volume in ordered:
            if _session(bar.bar_time) is None:
                continue
            if group and not _contiguous(group[-1][0], bar):
                group, highs, lows, pullback, warning_times, prior_state = [], [], [], {}, [], "RANGE"
                active_direction = None
                active_entry_price = None
            group.append((bar, D(volume)))
            confirmed = self._confirmed_pivot(group)
            if confirmed:
                (highs if confirmed.pivot_type == "HIGH" else lows).append(confirmed)
                events.append(VideoEvent(bar.bar_time, f"STRUCTURE_{confirmed.pivot_type}", "LONG" if confirmed.pivot_type == "LOW" else "SHORT", confirmed.pivot_price,
                                         self._placeholder(bar, volume), {"pivot_time": confirmed.pivot_time, "confirmed_time": confirmed.confirmed_time}))
                pivots=highs if confirmed.pivot_type=="HIGH" else lows
                if len(pivots)>=2:
                    volumes={item[0].bar_time:item[1] for item in group}
                    previous,current=pivots[-2],pivots[-1]
                    weakened=volumes.get(current.pivot_time,ZERO)<volumes.get(previous.pivot_time,ZERO)
                    diverged=(confirmed.pivot_type=="HIGH" and current.pivot_price>previous.pivot_price) or (confirmed.pivot_type=="LOW" and current.pivot_price<previous.pivot_price)
                    if weakened and diverged:
                        events.append(VideoEvent(bar.bar_time,"VOLUME_DIVERGENCE","SHORT" if confirmed.pivot_type=="HIGH" else "LONG",bar.close_price,self._placeholder(bar,volume),{"method":self.p.divergence_method,"confirmed_pivot_time":confirmed.pivot_time}))
            feature = self._feature(group, highs, lows, prior_state)
            state = feature.market_state
            if state != prior_state:
                if prior_state == "UPTREND" and state != "UPTREND": events.append(VideoEvent(bar.bar_time, "UPTREND_END", "SHORT", bar.close_price, feature))
                if prior_state == "DOWNTREND" and state != "DOWNTREND": events.append(VideoEvent(bar.bar_time, "DOWNTREND_END", "LONG", bar.close_price, feature))
                events.append(VideoEvent(bar.bar_time, f"{state}_CONFIRMED", "LONG" if state == "UPTREND" else "SHORT" if state == "DOWNTREND" else "LONG", bar.close_price, feature))
            prior_state = state
            current_events=self._bar_events(feature, group, highs, lows, pullback, warning_times)
            for item in current_events:
                if item.event_type in {"LONG_ENTRY","SHORT_ENTRY"} and active_direction is None:
                    active_direction=item.direction
                    active_entry_price=item.price
                elif active_direction and ((item.event_type=="STRUCTURE_LOW_BREAK" and active_direction=="LONG") or
                      (item.event_type=="STRUCTURE_HIGH_BREAK" and active_direction=="SHORT") or
                      item.event_type in {"BATTLE_CANDLE","REVERSAL_WARNING"}):
                    events.append(VideoEvent(bar.bar_time,"STRUCTURE_EXIT" if "BREAK" in item.event_type else "REVERSAL_EXIT",active_direction,bar.close_price,feature,{"trigger":item.event_type}))
                    active_direction=None
                    active_entry_price=None
            if active_direction and active_entry_price is not None:
                stopped=(active_direction=="LONG" and bar.close_price<=active_entry_price*(1-self.p.stop_distance_ratio)) or (active_direction=="SHORT" and bar.close_price>=active_entry_price*(1+self.p.stop_distance_ratio))
                if stopped:
                    events.append(VideoEvent(bar.bar_time,"STOP",active_direction,bar.close_price,feature,{"method":self.p.stop_method,"entry_price":active_entry_price}))
                    active_direction=active_entry_price=None
            events.extend(current_events)
            features.append(feature)
        # Replace placeholder references on pivot events with the actual feature at confirmation.
        by_time = {item.bar.bar_time: item for item in features}
        events = [VideoEvent(e.at, e.event_type, e.direction, e.price, by_time.get(e.at, e.feature), e.detail) for e in events]
        return features, events

    def _placeholder(self, bar, volume):
        body = abs(bar.close_price-bar.open_price)
        return VideoFeature(bar,D(volume),None,None,None,body,max(bar.open_price,bar.close_price),min(bar.open_price,bar.close_price),None,None,
                            bar.high_price-max(bar.open_price,bar.close_price),min(bar.open_price,bar.close_price)-bar.low_price,None,None,None,None,False,None,None,None,None,None,None,"RANGE","INSUFFICIENT_HISTORY")

    def _confirmed_pivot(self, group) -> Pivot | None:
        if self.p.pivot_method == "PIVOT_ZIGZAG":
            return None  # UNKNOWN confirmation semantics: supported as a selectable no-op, never guessed.
        right = int(self.p.pivot_method.rsplit("_", 1)[1])
        if len(group) < right * 2 + 1: return None
        center = len(group) - right - 1
        window = group[center-right:center+right+1]
        candidate = group[center][0]
        other = [item[0] for index, item in enumerate(window) if index != right]
        if all(candidate.high_price > item.high_price for item in other):
            return Pivot(candidate.bar_time,candidate.high_price,group[-1][0].bar_time,"HIGH",self.p.pivot_method,D(right))
        if all(candidate.low_price < item.low_price for item in other):
            return Pivot(candidate.bar_time,candidate.low_price,group[-1][0].bar_time,"LOW",self.p.pivot_method,D(right))
        return None

    def _feature(self, group, highs, lows, prior_state):
        bar, volume = group[-1]; closes=[x[0].close_price for x in group]; volumes=[x[1] for x in group]
        sma = sum(closes[-20:],ZERO)/20 if len(closes)>=20 else None
        slope = None
        if sma is not None and len(closes)>=20+self.p.sma_slope_window:
            old=sum(closes[-20-self.p.sma_slope_window:-self.p.sma_slope_window],ZERO)/20; slope=sma-old
        slope_ratio = None if sma in (None,ZERO) or slope is None else slope/sma
        direction = None if slope_ratio is None else "UP" if slope_ratio>=self.p.sma_slope_min_ratio else "DOWN" if slope_ratio<=-self.p.sma_slope_min_ratio else "FLAT"
        top=max(bar.open_price,bar.close_price); bottom=min(bar.open_price,bar.close_price); body=top-bottom
        above = None if sma is None or body==0 else max(ZERO,top-max(bottom,sma))/body
        below = None if sma is None or body==0 else max(ZERO,min(top,sma)-bottom)/body
        upper=bar.high_price-top; lower=bottom-bar.low_price; range_=bar.high_price-bar.low_price
        prior_bodies=[abs(x[0].close_price-x[0].open_price) for x in group[:-1]]
        expansion=self._expansion(body,bar,group,prior_bodies)
        avg=sum(volumes[-self.p.volume_avg_window:],ZERO)/self.p.volume_avg_window if len(volumes)>=self.p.volume_avg_window else None
        vr=None if avg in (None,ZERO) else volume/avg
        vc=None if len(volumes)<2 or volumes[-2]==0 else volume/volumes[-2]-1
        vs=None if len(volumes)<self.p.sma_slope_window+1 else (volume-volumes[-1-self.p.sma_slope_window])/self.p.sma_slope_window
        state="RANGE"
        if len(highs)>=2 and len(lows)>=2:
            if highs[-1].pivot_price>highs[-2].pivot_price and lows[-1].pivot_price>lows[-2].pivot_price and direction=="UP": state="UPTREND"
            elif highs[-1].pivot_price<highs[-2].pivot_price and lows[-1].pivot_price<lows[-2].pivot_price and direction=="DOWN": state="DOWNTREND"
        status="NORMAL" if sma is not None else "INSUFFICIENT_HISTORY"
        return VideoFeature(bar,volume,sma,slope,direction,body,top,bottom,above,below,upper,lower,_ratio(upper,body),_ratio(lower,body),_ratio(upper+lower,body),_ratio(range_,body),expansion,avg,vr,vc,vs,highs[-1] if highs else None,lows[-1] if lows else None,state,status)

    def _expansion(self, body, bar, group, prior):
        method=self.p.body_expansion_method
        if method=="BODY_EXP_NONE": return True
        if not prior: return False
        if method=="BODY_EXP_PREVIOUS": reference=prior[-1]
        elif method=="BODY_EXP_PREVIOUS_SAME_DIRECTION":
            bullish=bar.close_price>bar.open_price
            matches=[abs(x[0].close_price-x[0].open_price) for x in group[:-1] if (x[0].close_price>x[0].open_price)==bullish]
            if not matches:return False
            reference=matches[-1]
        elif method in {"BODY_EXP_AVG_N","BODY_EXP_PULLBACK_AVG"}: reference=sum(prior[-self.p.body_expansion_window:],ZERO)/min(len(prior),self.p.body_expansion_window)
        elif method=="BODY_EXP_PERCENTILE": reference=D(str(median(prior[-self.p.body_expansion_window:])))
        else: raise ValueError("unsupported body_expansion_method")
        return body >= reference*self.p.body_expansion_ratio

    def _bar_events(self, f, group, highs, lows, pullback, warnings):
        out=[]; bar=f.bar; bullish=bar.close_price>bar.open_price; bearish=bar.close_price<bar.open_price
        if f.volume_ratio is not None and f.volume_ratio>=self.p.volume_spike_ratio: out.append(VideoEvent(bar.bar_time,"VOLUME_SPIKE","LONG" if bullish else "SHORT",bar.close_price,f))
        if f.volume_ratio is not None and f.volume_ratio<=self.p.volume_drop_ratio: out.append(VideoEvent(bar.bar_time,"VOLUME_DROP","LONG" if bullish else "SHORT",bar.close_price,f))
        battle=(f.upper_wick_body_ratio is not None and f.lower_wick_body_ratio is not None and f.upper_wick_body_ratio>=self.p.battle_wick_body_ratio and f.lower_wick_body_ratio>=self.p.battle_wick_body_ratio and f.volume_ratio is not None and f.volume_ratio>=self.p.volume_spike_ratio)
        if battle:
            warnings.append(bar.bar_time); out += [VideoEvent(bar.bar_time,"BATTLE_CANDLE","SHORT" if bullish else "LONG",bar.close_price,f),VideoEvent(bar.bar_time,"REVERSAL_WARNING","SHORT" if bullish else "LONG",bar.close_price,f)]
            recent=[x for x in warnings if bar.bar_time-x<=timedelta(minutes=self.p.battle_repeat_bars)]
            if len(recent)>=2: out.append(VideoEvent(bar.bar_time,"NO_TRADE_WARNING","SHORT" if bullish else "LONG",bar.close_price,f))
        if f.upper_wick_body_ratio is not None and f.upper_wick_body_ratio>=self.p.battle_wick_body_ratio: out.append(VideoEvent(bar.bar_time,"UPPER_WICK_WARNING","SHORT",bar.close_price,f))
        if f.lower_wick_body_ratio is not None and f.lower_wick_body_ratio>=self.p.battle_wick_body_ratio: out.append(VideoEvent(bar.bar_time,"LOWER_WICK_WARNING","LONG",bar.close_price,f))
        if f.sma20 is not None:
            distance=min(abs(bar.low_price-f.sma20),abs(bar.high_price-f.sma20))/f.sma20
            if f.market_state=="UPTREND" and (distance<=self.p.pullback_distance_ratio or bar.low_price>=f.sma20*(1-self.p.pullback_allow_below_ratio) and bar.low_price<=f.sma20):
                pullback["LONG"]=bar.bar_time; out.append(VideoEvent(bar.bar_time,"SMA_PULLBACK","LONG",bar.close_price,f))
                if f.volume_ratio is not None and f.volume_ratio<=D("1"): out.append(VideoEvent(bar.bar_time,"NORMAL_PULLBACK_VOLUME","LONG",bar.close_price,f))
            if f.market_state=="DOWNTREND" and (distance<=self.p.pullback_distance_ratio or bar.high_price<=f.sma20*(1+self.p.pullback_allow_below_ratio) and bar.high_price>=f.sma20):
                pullback["SHORT"]=bar.bar_time; out.append(VideoEvent(bar.bar_time,"SMA_PULLBACK","SHORT",bar.close_price,f))
                if f.volume_ratio is not None and f.volume_ratio<=D("1"): out.append(VideoEvent(bar.bar_time,"NORMAL_PULLBACK_VOLUME","SHORT",bar.close_price,f))
            for direction, is_reclaim in (("LONG",bar.close_price>f.sma20 and bullish),("SHORT",bar.close_price<f.sma20 and bearish)):
                start=pullback.get(direction)
                if start and 0 < (bar.bar_time-start).total_seconds()/60 <= self.p.reclaim_max_bars and is_reclaim:
                    out.append(VideoEvent(bar.bar_time,"SMA_RECLAIM",direction,bar.close_price,f))
                    body_ok=(direction=="LONG" and (f.body_above_ratio or ZERO)>=self.p.body_above_ratio) or (direction=="SHORT" and (f.body_below_ratio or ZERO)>=self.p.body_above_ratio)
                    if body_ok: out.append(VideoEvent(bar.bar_time,"BODY_VALID",direction,bar.close_price,f))
                    if f.body_expansion: out.append(VideoEvent(bar.bar_time,"BODY_EXPANSION",direction,bar.close_price,f))
                    blockers={e.event_type for e in out if e.event_type in {"BATTLE_CANDLE","NO_TRADE_WARNING"}}
                    structure_ok=self.p.ablation=="NO_STRUCTURE" or f.market_state==("UPTREND" if direction=="LONG" else "DOWNTREND")
                    body_required=self.p.ablation!="NO_BODY"; volume_required=self.p.ablation!="NO_VOLUME"
                    volume_ok=(not volume_required) or f.volume_ratio is None or f.volume_ratio>self.p.volume_drop_ratio
                    if structure_ok and (body_ok or not body_required) and volume_ok and not blockers:
                        out += [VideoEvent(bar.bar_time,f"{direction}_READY",direction,bar.close_price,f),VideoEvent(bar.bar_time,f"{direction}_ENTRY",direction,bar.close_price,f)]
                    pullback.pop(direction,None)
        if highs and bar.close_price>highs[-1].pivot_price: out.append(VideoEvent(bar.bar_time,"STRUCTURE_HIGH_BREAK","LONG",bar.close_price,f))
        if lows and bar.close_price<lows[-1].pivot_price: out.append(VideoEvent(bar.bar_time,"STRUCTURE_LOW_BREAK","SHORT",bar.close_price,f))
        return out


def execution_direction(source_direction: str, stock_code: str) -> str | None:
    if source_direction == "LONG": return "LONG"  # 0197X0 is retained as the opposite-product benchmark.
    if source_direction == "SHORT": return "VIRTUAL_SHORT" if stock_code=="000660" else "LONG"
    return None


def measure_event(event: VideoEvent, target_bars: Mapping[datetime, MinuteBar], stock_code: str) -> dict:
    direction=execution_direction(event.direction,stock_code)
    entry=target_bars.get(event.at)
    if direction is None: return {"data_status":"INVALID_SOURCE","execution_direction":None}
    if entry is None: return {"data_status":"TRADE_PRICE_MISSING","execution_direction":direction}
    sign=D("-1") if direction=="VIRTUAL_SHORT" else D("1")
    result={"data_status":"NORMAL","execution_direction":direction,"trade_price":entry.close_price}
    future=[]
    for minutes in (1,3,5,10,20,30):
        bar=target_bars.get(event.at+timedelta(minutes=minutes)); result[f"return_{minutes}m"]=None if bar is None else (bar.close_price/entry.close_price-1)*sign
        if bar is not None: future.append(bar)
    if future:
        returns=[]
        for bar in future:
            if direction=="VIRTUAL_SHORT": returns.extend((entry.close_price/bar.low_price-1,entry.close_price/bar.high_price-1))
            else: returns.extend((bar.high_price/entry.close_price-1,bar.low_price/entry.close_price-1))
        result["mfe"],result["mae"]=max(returns),min(returns)
    else: result["mfe"],result["mae"]=None,None
    return result
