# VIDEO_STRATEGY V1

## 목적과 격리 원칙

`VIDEO_STRATEGY`는 저장된 공식 RAW만 읽는 독립 연구전략이다. 기존 Multi-MA, 일봉 전략, SMA alert, 주문, ntfy, `GLOBAL_TRADE_YN`에는 연결하지 않는다. 실행마다 독립 `research_run`을 만들며 `parameters.strategy_family=VIDEO_STRATEGY`, `strategy_version=V1`로 기존 run과 구분한다. 기존 research/RAW 행은 UPDATE/DELETE하지 않는다.

source는 항상 `000660`이다. 판단 feature/event/entry/exit/stop은 000660 RAW로만 만든다. 동일 source event를 exact timestamp의 `000660`, `0193T0`, `0197X0` 가격에 투영한다. 가격이 없으면 이전/다음 가격이나 source 가격으로 보간하지 않고 `TRADE_PRICE_MISSING`으로 남긴다.

## 규칙 분류

| 영역 | 분류 | V1 정의 |
|---|---|---|
| family/version/source/timeframe | FIXED | VIDEO_STRATEGY/V1, 000660, 1MIN |
| MA | FIXED | SMA20 |
| slope window/min ratio | PARAMETER | 기본 3, 0.0005 |
| pivot | MULTI_TEST | FRACTAL 2/3/5; ZIGZAG은 의미가 UNKNOWN이므로 선택 가능하되 pivot을 추측 생성하지 않음 |
| market state | FIXED | HH+HL+SMA UP=UPTREND, LH+LL+SMA DOWN=DOWNTREND, 나머지 RANGE |
| pullback/reclaim | PARAMETER | 거리, SMA 아래 허용폭, 최대 bar, confirm method |
| body above/below | PARAMETER | 기본 0.50; 0.50/0.60/0.70/0.80 sweep 가능 |
| body expansion | MULTI_TEST | NONE/PREVIOUS/PREVIOUS_SAME_DIRECTION/AVG_N/PULLBACK_AVG/PERCENTILE |
| volume/RVOL | MULTI_TEST | SIMPLE 구현; TIME_OF_DAY는 충분한 동일 시각 과거 표본이 없으면 계산 금지 |
| spike/drop | PARAMETER | 기본 2.0/0.5; grid로 실행 |
| divergence | MULTI_TEST | PIVOT 기본. SLOPE/WINDOW는 parameter snapshot 대상이며 별도 run에서 검증 |
| battle candle | PARAMETER | 양쪽 wick/body와 volume spike. 색상은 사용하지 않음 |
| short | FIXED concept + PARAMETER | DOWNTREND/reclaim/strong bearish body. breakout chase 숫자는 parameter |
| exit | MULTI_TEST | divergence/structure/reversal/battle/SMA/stop reason 분리 |
| stop | MULTI_TEST/PARAMETER | WICK/CLOSE/BODY/DISTANCE/STRUCTURE; 기본 STOP_CLOSE + distance 1% |
| capital/cost | PARAMETER | run JSON snapshot. 기존 research fee/sell tax/slippage 정책 사용 |
| wick volume | EXPERIMENTAL | 불완전 5초 REST 체결이므로 APPROXIMATE. VIDEO_BASE 조건에는 사용 금지 |
| program/execution strength | ENHANCED VARIANT | VIDEO_BASE에 사용 금지; 별도 variant run만 허용 |

UNKNOWN을 임의 숫자로 고정하지 않는다. `PIVOT_ZIGZAG`은 threshold 의미가 공식화되기 전에는 event를 만들지 않는다.

## Canonical feature

`research_feature.feature_detail`에 run별 canonical 값을 저장한다.

- `sma20`, `sma20_slope`, `sma20_direction`
- `body_size`, `body_top`, `body_bottom`, `body_above_ratio`, `body_below_ratio`
- `upper_wick`, `lower_wick`, wick/body ratios, range/body ratio
- `body_expansion`
- `volume_avg`, `volume_ratio`, `volume_change_ratio`, `volume_slope`
- 마지막 확인 pivot high/low와 각 `pivot_time`, `confirmed_time`, method/parameter
- `market_state`, `data_status`

body가 0이면 wick/body와 range/body ratio는 NULL이다. 데이터가 부족하면 `INSUFFICIENT_HISTORY`; 세션/분봉 연속성이 끊기면 과거 window와 state를 reset하며 gap을 건너 계산하지 않는다.

## Pivot와 look-ahead 방지

Fractal N pivot은 좌 N개와 우 N개 bar가 모두 도착한 `confirmed_time`에 처음 공개된다. `pivot_time`은 실제 고점/저점 시각이지만, `observation_time < confirmed_time`인 판단에서는 절대 사용하지 않는다. engine은 한 번의 시간순 loop에서 confirmation 시점에만 pivot을 state에 넣는다. 미래 bar는 event 발생 후 성과 측정 함수에만 전달된다.

## State machine

확인된 최근 두 high와 low 및 SMA 방향으로 UPTREND/DOWNTREND/RANGE를 결정한다. transition event는 `UPTREND_CONFIRMED`, `DOWNTREND_CONFIRMED`, `RANGE_CONFIRMED`, `UPTREND_END`, `DOWNTREND_END`다. `UPTREND_END`는 곧바로 DOWNTREND가 아니며 반대도 동일하다.

## Event, Entry, Exit, Stop

event는 기존 `research_signal_event`에 `strategy_code=VIDEO_STRATEGY_V1`과 detail JSON으로 저장한다.

- Structure: `STRUCTURE_HIGH/LOW`, `STRUCTURE_HIGH_BREAK/LOW_BREAK`
- SMA: `SMA_PULLBACK`, `SMA_RECLAIM`
- Candle: `BODY_VALID`, `BODY_EXPANSION`, `UPPER_WICK_WARNING`, `LOWER_WICK_WARNING`, `BATTLE_CANDLE`, `NO_TRADE_WARNING`
- Volume: `VOLUME_SPIKE`, `VOLUME_DROP`, `NORMAL_PULLBACK_VOLUME`, `VOLUME_DIVERGENCE`
- Entry: `LONG_READY/ENTRY`, `SHORT_READY/ENTRY`
- Exit/stop: `STRUCTURE_EXIT`, `REVERSAL_EXIT`, `STOP` 및 확장 가능한 명시적 exit reason

LONG은 UPTREND→pullback→제한 bar 안 reclaim→bullish/body 조건→volume/no-warning 조건의 순서다. SHORT은 좌우 단순 반전이 아니라 DOWNTREND와 확인된 구조 저점, SMA 회복 실패 방향, bearish/body 조건을 사용한다. 같은 event로 source와 execution 개념을 섞지 않는다.

## Projection과 성과

- source LONG: 000660 LONG, 0193T0 LONG, 0197X0 LONG 반대상품 benchmark.
- source SHORT: 000660 VIRTUAL_SHORT, 0197X0 LONG, 0193T0 LONG 반대상품 benchmark.
- 진입/청산은 source event와 같은 timestamp의 target close만 사용한다.
- event별 1/3/5/10/20/30분 forward return을 target별로 저장한다.
- MFE/MAE는 event 후 존재하는 target candle의 HIGH/LOW로 계산하며 종가 extrema를 사용하지 않는다.
- 미래 데이터 접근은 `measure_event`에 격리되어 entry 판단 코드와 경계가 분리된다.
- cycle 비용은 run의 `fee_rate`, `sell_tax_rate`, `slippage_rate`, `capital_policy`, `cost_policy_version` snapshot을 사용한다. `gross_realized_profit - buy_fee - sell_fee - sell_tax = realized_profit`을 유지한다.

## Parameter sweep와 Ablation

각 조합은 별도 `research_run`이다. CLI 인자로 선택 실행하며 무제한 Cartesian product를 자동 생성하지 않는다. 비교 API는 run별/target별 event count, 5분 평균·median return, 평균 MFE/MAE를 제공한다. cycle이 생성된 run은 기존 cycle/일별 성과 구조에서 win rate, profit factor, expectancy, drawdown, holding time을 계산할 수 있다.

Ablation은 `FULL`, `NO_STRUCTURE`, `NO_BODY`, `NO_VOLUME`, `NO_WICK`를 별도 run parameter로 저장한다. NO_*는 해당 판단군만 제거하고 source/price/cost/session 규칙은 바꾸지 않는다.

## Dashboard/API

- 화면: `/research/video-strategy`
- API: `/research/video-strategy/api/runs`, `/replay?run_id=...`, `/compare`
- 기존 `/`, `/research/performance`, `/research/daily`, `/admin/backfill` payload와 route를 변경하지 않는다.
- 화면은 run 선택, source 가격/SMA20/event replay, candle click 판단근거 JSON, event별 세 target 성과, parameter/ablation 비교를 제공한다.

## Replay 실행

테스트 DB에서 먼저 additive DDL을 적용한 후 실행한다.

```text
python scripts/research/apply_research_ddl.py
python scripts/research/run_video_strategy.py --start-date YYYY-MM-DD --end-date YYYY-MM-DD
```

실행은 test 이름의 DB만 허용한다. 운영 배포, 주문, alert는 이 명령에 포함되지 않는다.

## Backfill 및 데이터 한계

기존 `/admin/backfill`로 000660/0193T0/0197X0 분봉을 수집할 수 있다. 필요한 기간이 부족하면 자동 대규모 backfill하지 않고 종목·요구기간·현재기간·누락기간만 보고한다.

Wick Volume Zone은 `raw_stock_execution`이 완전 tape가 아닌 5초 REST polling이고 현재 주로 000660만 보유하므로 `APPROXIMATE`다. upper zone은 `max(open,close)~high`, lower zone은 `low~min(open,close)`로 정의하되 VIDEO_BASE 진입/청산에는 사용하지 않는다. Program/체결강도도 `VIDEO_PROGRAM`, `VIDEO_EXECUTION_STRENGTH`, `VIDEO_PROGRAM_EXECUTION_STRENGTH` run에서만 연구한다.
