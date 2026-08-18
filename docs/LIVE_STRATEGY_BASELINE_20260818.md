# Trading System V2 — LIVE 전략 공식 기준선

**STATUS: FROZEN**  
**SOURCE OF TRUTH: YES**  
**Baseline date: 2026-08-18**  
**전략 연구 기준일: 2026-08-16~17 + Champion/Golden 후속 감사**

> 이 문서는 현재 LIVE Champion 전략 4개의 공식 정의다.  
> 코드·DB·Dashboard·과거 대화와 이 문서가 충돌하면 전략을 임의 수정하지 말고 mismatch를 감사한다.  
> 모호한 서술보다 **Champion을 실제로 산출한 historical procedure / Golden / 실제 trade**를 우선한다.

---

## 1. LIVE Champion 요약

| # | 판단 본주 | 신호 | 진입 전략 | 실행상품 | 청산 |
|---|---|---|---|---|---|
| 1 | 000660 SK하이닉스 | SHORT | S3_VOLUME_CLIMAX_REVERSAL / 5봉 0.8% / RVOL 2.0x | 0197X0 LONG | STRUCTURE_3BAR_MAX30_STOP_2.5 |
| 2 | 000660 SK하이닉스 | SHORT | 위와 동일한 shared S3 ENTRY | 0197X0 LONG | STRUCTURE_5BAR_MAX30_STOP_2.5 |
| 3 | 005930 삼성전자 | LONG | S1_OR_PULLBACK_RESTART / 30분 OR | 0193W0 LONG | PULLBACK_LOW_BREAK_WITHIN30_EOD |
| 4 | 005930 삼성전자 | SHORT | S2_FAILED_OR_VWAP / 30분 OR | 0193L0 LONG | FIXED_30 |

**중요:** 하이닉스 S3 3BAR와 5BAR는 비교전략이 아니라 둘 다 LIVE다. 진입 Decision은 하나를 공유하고 Exit Policy만 분리한다.

---

## 2. 공통 실행 원칙

- 전략 신호/청산 판단은 **본주 완료 1분봉**으로 한다.
- 예: 10:01 봉 완료 → 10:02:01경 완료봉 수집/판단 → 주문.
- 실제 실행상품 가격은 LIVE에서 **주문 요청 / ACK / 체결 / 부분체결 / 주문원장**으로 확정한다.
- LIVE 도입을 이유로 5초 snapshot 수집대상을 확대하지 않는다. 기존 snapshot은 연구/사후검증에만 선택적으로 사용한다.
- `collected_at` 등 실제 수집 가능시각 이후의 정보만 사용하며 look-ahead를 금지한다.
- ENTRY target time의 공통 계약:
  - signal bar 이후 **signal source의 다음 실제 completed 1MIN bar_time**을 `entry_target_time`으로 확정한다.
  - Historical Adapter는 그 동일 timestamp의 execution product bar를 사용한다.
  - execution product bar가 없다고 Strategy Core가 임의로 다음 execution bar로 이동시키지 않는다.

---

# 3. LIVE #1 / #2 — HYNIX S3 SHORT

## 3.1 공통 ENTRY

**Strategy:** `S3_VOLUME_CLIMAX_REVERSAL`  
**Signal source:** `000660`  
**Signal direction:** SHORT  
**Execution:** `0197X0` LONG  
**Candidate window:** 09:10~14:50  
**Move threshold:** 0.8%  
**RVOL threshold:** 2.0x

### Climax 조건
- `ret5bar >= +0.8%`
  - `ret5bar`는 **row-based**: 현재 봉에서 5개의 실제 이전 completed row 기준 close.
- `RVOL20 >= 2.0`
  - RVOL20도 **row-based**: 직전 20개 실제 completed row의 평균 거래량.
- body > 0
- `upper_wick / body >= 0.5`
- 당일 최초 SHORT climax를 사용.

### Confirm 조건
Climax 이후 최대 8분 이내 최초 bar 중:
- `current high <= climax high`
- `close < min(climax open, climax close)`
- `close < open`

Confirm bar가 signal bar다.

### ENTRY
- `signal_time = confirm bar_time`
- `entry_target_time = confirm 이후 000660의 다음 실제 completed 1MIN bar_time`
- Historical Adapter는 해당 timestamp의 `0197X0` bar를 entry execution 후보로 사용.
- 3BAR/5BAR는 **별도 진입 계산 금지**. 동일 shared ENTRY Decision을 사용.

## 3.2 STOP_2.5

- STOP 판단 기준 상품: `0197X0`
- entry reference: historical actual entry bar OPEN
- trigger:
  - `0197X0 completed CLOSE <= entry_price * 0.975`
- intrabar LOW 기준 금지.
- 000660 기준 금지.
- STOP 실행은 다음 실제 존재하는 `0197X0` 1MIN bar OPEN.

## 3.3 STRUCTURE_3BAR — LIVE #1

**공식 의미: row 3개가 아니라 clock-time 3분 window.**

Structure check 시작: entry + 5분.

현재 `000660` completed CLOSE가 아래 window의 실제 존재 봉 HIGH MAX보다 높으면 structure exit candidate:

`current_time - 3 minutes <= previous_bar_time < current_time`

- 데이터 gap이 있어도 window 밖 오래된 봉을 끌어와 3개를 채우지 않는다.
- window 안에 실제 존재하는 봉만 사용한다.

최종 Exit Policy:
`STRUCTURE_3BAR_MAX30_STOP_2.5`

## 3.4 STRUCTURE_5BAR — LIVE #2

**공식 의미: row 5개가 아니라 clock-time 5분 window.**

현재 `000660` completed CLOSE가 아래 window의 실제 존재 봉 HIGH MAX보다 높으면 structure exit candidate:

`current_time - 5 minutes <= previous_bar_time < current_time`

- 데이터 gap이 있어도 실제 5개 봉을 채우지 않는다.
- window 안의 실제 존재 봉만 사용한다.

최종 Exit Policy:
`STRUCTURE_5BAR_MAX30_STOP_2.5`

## 3.5 S3 Exit arbitration

Exit 후보:
- STRUCTURE
- STOP
- MAX30

원칙:
- 실제 execution timestamp가 가장 빠른 이벤트를 선택.
- 동일 execution timestamp면 **STRUCTURE 우선**.
- 최대 보유는 30분.

---

# 4. LIVE #3 — SAMSUNG S1 LONG

**Strategy:** `S1_OR_PULLBACK_RESTART`  
**Signal source:** `005930`  
**Signal direction:** LONG  
**Execution:** `0193W0` LONG  
**OR:** 30분

## 4.1 OR

정규장:
`09:00 <= bar_time < 09:30`

- `or_high = HIGH 최대값`
- `or_low = LOW 최소값`

## 4.2 BREAKOUT

OR 종료 이후 최초 bar 중:
- `close > or_high`
- `previous close <= or_high`
- `close > open`
- `body_ratio >= 0.50`

`body_ratio = abs(close-open) / (high-low)`

최초 1건만 사용.

## 4.3 PULLBACK

Breakout 이후 30분 이내 최초 bar 중:
- `low <= or_high * 1.003`
- `close >= or_high * 0.997`
- `volume <= breakout volume`

최초 1건 선택.

이 bar의:
- `pullback_high = high`
- `pullback_low = low`

`pullback_low`는 이후 공식 EXIT invalidation level이다.

## 4.4 RESTART

Pullback 이후 20분 이내 최초 bar 중:
- `close > open`
- `close > previous bar high`
- `close > or_high`
- `body_ratio >= 0.50`
- `volume >= pullback volume * 1.10`

최초 1건 선택.

- `signal_time = restart bar_time`
- `entry_target_time = restart 이후 005930의 다음 실제 completed 1MIN bar_time`
- Historical Adapter는 그 timestamp의 `0193W0` bar를 execution 후보로 사용.

## 4.5 상태전이 / 재진입

`OR → 최초 BREAKOUT → 해당 breakout의 최초 PULLBACK → 해당 pullback의 최초 RESTART → ENTRY`

- 같은 거래일에서 두 번째 breakout 재탐색 금지.
- strategy instance당 거래일 최초 valid setup 1개.
- signal 생성 후 당일 재진입 없음.

## 4.6 공식 EXIT — PULLBACK_LOW_BREAK_WITHIN30_EOD

**Champion 산출 기준은 “EOD까지 감시”가 아니라 “진입 후 첫 30분만 감시”다.**

진입 후 첫 30분 동안:
- `005930 completed CLOSE < pullback_low`
- 발생하면 invalidation → EXIT Decision
- 다음 실제 `0193W0` 1MIN bar에서 실행.

30분 안에 invalidation이 없으면:
- 이후 pullback_low 구조손절 없음.
- 15:19 EOD EXIT.

Historical/연구:
- 정확한 15:19 실행상품 봉이 없으면 15:19 이전 마지막 실제 실행상품 분봉을 사용.

LIVE:
- 실제 가격은 Order/Fill이 책임진다.

### Champion audit의 결정적 증거
2026-06-22:
- Entry 09:42
- pullback_low 350,000
- 최초 close < pullback_low: 13:36
- Champion 실제 exit: 15:19 EOD
- 따라서 30분 이후에는 pullback_low를 더 이상 손절로 사용하지 않는 B 규칙이 확정됨.

## 4.7 선정 성과 / 연구 보존

LIVE `PULLBACK_LOW_BREAK_WITHIN30_EOD`:
- 6거래
- 승률 83.3%
- 1천만원 독립복리 기준 12,791,026원
- 복리 +27.910%
- MDD -2.181%

연구 보존 `FIXED_30`:
- 6거래
- 승률 100%
- 복리 +22.951%

**FIXED_30은 삭제하지 않고 RESEARCH로 유지한다.**

---

# 5. LIVE #4 — SAMSUNG S2 SHORT

**Strategy:** `S2_FAILED_OR_VWAP`  
**Signal source:** `005930`  
**Signal direction:** SHORT  
**Execution:** `0193L0` LONG  
**OR:** 30분

## 5.1 OR
`09:00 <= bar_time < 09:30`

- `or_high = HIGH 최대값`

## 5.2 UPSIDE BREAKOUT

OR 종료 이후 최초 bar 중:
- `high > or_high`
- `close >= or_high`

최초 1건 선택.

## 5.3 FAILED BREAKOUT

Breakout 이후 20분 이내 최초 bar 중:
- `close < or_high`
- `close < open`
- `close > VWAP`

최초 1건 선택.

- `signal_time = failed-breakout bar_time`
- `entry_target_time = failed-breakout 이후 005930의 다음 실제 completed 1MIN bar_time`
- Historical Adapter는 동일 timestamp의 `0193L0` bar를 사용.

## 5.4 VWAP 공식

`typical_price = (high + low + close) / 3`

`VWAP = cumulative SUM(typical_price * volume) / cumulative SUM(volume)`

- 당일 누적.
- signal bar 시점까지의 누적값 사용.
- close×volume 방식이 아님.

## 5.5 상태 / 재진입

`OR → 최초 UPSIDE BREAKOUT → 그 breakout 이후 최초 FAILED BREAKOUT → ENTRY`

- 동일 거래일 이후 breakout 재탐색 금지.
- strategy instance당 거래일 최초 valid setup 1개.
- signal 생성 후 당일 재진입 없음.

## 5.6 EXIT

`FIXED_30`

- entry 기준 30분 후 EXIT Decision.
- Historical 실행가격은 Historical Adapter.
- LIVE 실행가격은 Order/Fill.

### LIVE에서 제외된 청산안
다음은 성능이 크게 악화되어 LIVE에 적용하지 않는다:
- 본주 OR 재회복 손절
- breakout high 재돌파 손절
- EOD 보유

### 현재 최고
- 7거래
- 승률 71.4%
- 복리 +15.189%
- MDD 약 -3.7%

---

# 6. 자본 운용

- LIVE 4전략은 **각각 독립 전략자본**으로 관리.
- 초기 전략자본: **각 1,000,000원**
- 각 전략은 독립 복리.
- 다른 전략 수익을 섞어 투자금액을 늘리지 않는다.
- 프로젝트 기준 규제/기본예탁 목적 현금 reserve **30,000,000원**은 전략자본과 별도다.
- reserve를 전략 position sizing 자본으로 간주하지 않는다.

---

# 7. LIVE가 아닌 것

- `0193T0` 하이닉스 레버리지: 연구/수집 유지 가능, **LIVE 아님**.
- `0194N0`: 과거 연구/초기 문서 흔적, **최종 삼성 LONG 실행상품 아님**.
- `S4_AFTERNOON_MOMENTUM`: 연구전략, LIVE Champion 아님.
- 다중 MA (`SIGNAL_1/2/3`, `ACCUMULATED...`) 및 `VIDEO_STRATEGY_V1`: 별도 연구/분석 계통.

---

# 8. 연구전략 보존 원칙

LIVE는 4개지만 아래는 계속 RESEARCH로 보존한다:
- `S1_OR_PULLBACK_RESTART`
- `S2_FAILED_OR_VWAP`
- `S3_VOLUME_CLIMAX_REVERSAL`
- `S4_AFTERNOON_MOMENTUM`
- 모든 Variant / Exit

주요 Exit 연구군:
- FIXED_10 / 20 / 30
- STRUCTURE_2BAR_MAX30
- STRUCTURE_3BAR_MAX30
- STRUCTURE_5BAR_MAX30
- STRUCTURE_2BAR_EOD
- STRUCTURE_3BAR_EOD
- STRUCTURE_5BAR_EOD
- STRUCTURE_3BAR_MAX30_STOP_1.0 / 1.5 / 2.0 / 2.5 / 3.0
- STRUCTURE_5BAR_MAX30_STOP_1.0 / 1.5 / 2.0 / 2.5 / 3.0
- NO_STOP_EOD
- S1 전용 PULLBACK_LOW 계열

원칙:
- 연구전략을 과거 성과 때문에 삭제하지 않는다.
- LIVE 여부와 연구 성과를 분리한다.
- 자동으로 Champion을 교체하지 않는다.
- 사람이 결과를 보고 승격/강등한다.
- 과거 run/result는 삭제하지 않는다.

---

# 9. 전략 변경 통제 — 육하원칙 + IMPACT

LIVE 전략 변경은 아래 기록과 사용자 명시 승인 없이는 금지한다.

## WHO — 누가
- 변경 제안자
- 검증 담당
- 최종 승인자

## WHEN — 언제
- 연구기간
- 검증일
- 승인일
- 적용일

## WHERE — 어디를
- strategy_id / strategy_code
- ENTRY / EXIT / parameter / execution mapping 중 변경 위치

## WHAT — 무엇을
- 기존 규칙
- 변경 규칙
- 정확한 parameter diff

## WHY — 왜
- 발견된 문제
- 변경 가설
- 근거 데이터
- 기존 전략을 유지하면 발생하는 문제

## HOW — 어떻게 검증했는가
- 백테스트 기간
- 거래 건수
- 기존/신규 성과
- MDD
- 비용
- Golden
- Replay
- Forward
- gap/edge fixture

## IMPACT — 무엇이 달라지는가
- 신호 수
- 진입/청산 시점
- 평균/최대 보유시간
- 실행상품
- 자본/위험
- 수익률/MDD
- 기존 Golden
- 기존 Research 결과와 비교 가능성

### 승인 순서
`RESEARCH Variant → 동일 데이터 비교 → Golden/Replay → 성과·리스크 비교 → 육하원칙+IMPACT 작성 → 사용자 명시 승인 → Baseline version-up → 코드 변경`

**LIVE 코드를 먼저 바꾸고 나중에 이유를 적는 방식은 금지한다.**

---

# 10. Source-of-Truth 우선순위

전략 의미가 충돌할 경우:

1. Champion을 실제로 산출한 historical procedure / Golden / 실제 trade
2. Strategy Core canonical exact-match 결과
3. FROZEN Baseline 문서
4. 일반 설명/대화/화면 문구

단, 1~2의 구현 자체를 바꾸려면 반드시 전략 변경 절차를 거친다.

---

# 11. 검증 근거 메모

- LIVE Champion 최종 수정본: 2026-08-17
- Golden v1.0/v1.1 exact 검증: S1 6/6, S2 7/7, S3 3BAR 10/10, S3 5BAR 10/10, shared S3 entry 10/10
- S3 structure semantics:
  - Champion DB procedure와 Golden generator는 3/5 **clock-minute window**
  - row-based actual N bars가 아님
- S1 exit semantics:
  - Champion procedure/Golden/실제 trade가 **진입 후 30분만 pullback_low 감시**를 증명
  - 2026-06-22 trade가 A(EOD 지속감시)와 B(30분 감시)를 명확히 구분
- Golden artifact 관련 commit: `6fbed4f`

---

# 12. 문서 범위

이 문서는 **전략 정의**만 봉인한다.

다음은 별도 운영 문서 범위:
- LIVE instance 등록
- Strategy Runtime
- Capital Ledger
- Broker Adapter
- Fill/Position/Reconciliation
- ORDER_SMOKE_TEST
- KIS transport
- 실제 주문 승인/게이트

전략 정의와 운영 구현상태를 섞어서 “운영전략 수”를 재해석하지 않는다.
