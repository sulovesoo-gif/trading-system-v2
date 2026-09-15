# Leadership STOCK 매도세금 정정 / 과거 Snapshot 실행

## 사용자 전달 운영상태 (2026-09-15)

- Ubuntu main에는 Leadership 7da9af5와 INTEGRATED retention 98db2a3가 반영됨.
- retention 미래 chunk 1일 migration은 사용자가 SQL_PASS 확인. timer 설치/enable은 미확정.
- boot volume/filesystem 확장 완료. 이전 disk-full을 현재 장애로 취급하지 않음.
- trading-system-v2-timescaledb-test / trading_system_v2_test는 **운영 DB**이며 시험용이 아님.
- 이번 수정은 PURGE/운영 LIVE operation/Route/자본/SEND/신호/기존 PAPER에 손대지 않음.

## 비용만 변경

매수대금 B, 매도대금 S일 때:

```
gross = S - B
buy_fee = B * 0.000140527
sell_fee = S * 0.000140527
sell_tax = S * 0.002
net = gross - buy_fee - sell_fee - sell_tax
```

Leadership는 기존 Decimal 정밀도 유지. 별도 반올림/슬리피지 규칙 추가 없음.
net을 EXIT 시 자본에 반영하므로 이후 ENTRY 정수수량·수익률·MDD·순위에도 반영됨.
`ResearchCostPolicy.stock_sell_tax_rate`만 0.002로 수정하고 ETF/ETN/슬리피지는 유지.
기존 for_stock의 종목 분류 범위는 이번 요청에서 확장하지 않음.

새 Leadership VERSION: `LEADERSHIP_V2_STOCK_SELL_TAX_002`.
source_audit.cost_contract에도 양방향 수수료와 매도전용 세금을 기록.
Snapshot 컬럼 추가/migration 변경 없음. 구버전 Snapshot이 있으면 immutable conflict로 차단.

## 2026-09-15 운영 READ ONLY 결과

```
to_regclass(public.flow_v3_leadership_run)      = NULL
to_regclass(public.flow_v3_leadership_snapshot) = NULL
FLOW_LEADERSHIP_CAPITAL common_code rows       = 0
활성 본주 LONG PAPER 최초 entry_signal_date    = 2026-08-31
```

동일 날짜 두 본주 모두 KIS/KRX/1MIN 가격 존재:
2026-08-31, 09-01, 09-02, 09-03, 09-04, 09-07, 09-08, 09-09, 09-10, 09-11, 09-14.
이는 재생 대상일 확인이며 모든 전략의 가격/RAW 완전성 PASS를 뜻하지 않음.
그 판정은 기존 compute의 정책별 source_audit를 사용함.

**실제 운영 Snapshot 생성 0건. TOP50/TOP5 성과 결과 없음.**
최초 blocker는 기존 Leadership schema/common-code 미설치.
임의 대체 코드·임시 자본값·SQL 성과 INSERT로 우회하지 않음.
비용 정정용 새 migration은 불필요하지만 기존
`database/migrations/20260915_flow_v3_leadership.sql`의 운영 적용과 제한된 전용
READ/WRITE DSN 준비가 선행되어야 함. 이번에는 운영 DDL/DML/배포/재시작하지 않음.

## 기존 runner를 사용하는 날짜별 backfill

새 wrapper는 거래일 열거/선행검사/기존 CLI 반복 호출만 수행함.
compute/publish, asof 상한, HOLD 연속 재생, RESEARCH_START_LOSES_HOLD_HISTORY는 그대로 유지.
특정 일자 실패 시 이후 날짜 중단. 성공한 앞 날짜는 지우지 않으며 재실행은 기존
UNCHANGED/IMMUTABLE_SNAPSHOT_CONFLICT 계약 적용.
REGULAR과 extended policy 계산은 분리 유지. extended 부족 시 REGULAR을 버리지 않음.

운영 선행조건과 수정 코드 반영 후 사용할 명령(이번에는 실행하지 않음):

```bash
# 기존 전용 LEADERSHIP_READ_DSN / LEADERSHIP_WRITE_DSN 환경을 사용한다.
# 기본은 SELECT-only plan. 실제 날짜/자본축/구버전 충돌을 먼저 확인한다.
venv/bin/python scripts/research/backfill_flow_v3_leadership.py --through 2026-09-14

# compute-only: 기존 runner --date / --research-start 반복, 저장 없음
venv/bin/python scripts/research/backfill_flow_v3_leadership.py --through 2026-09-14 --compute

# 제한된 Leadership writer만 사용. 기존 runner의 권한검사 유지
venv/bin/python scripts/research/backfill_flow_v3_leadership.py --through 2026-09-14 --write-snapshot

# 최신 새 비용 VERSION Snapshot의 REGULAR Top50와 기존 LIVE TOP5 조회
venv/bin/python scripts/research/backfill_flow_v3_leadership.py --through 2026-09-14 --report-only
```

보고서는 공통코드 기본 자본축(현재 계약 600만원)을 읽고 Daily/Weekly/Monthly를
각각 출력한다. 기간을 혼합한 하나의 순위로 오해하지 않도록 분리했다.
각 항목: rank, strategy_id, stock_code, compound_return, final_capital, net_profit,
trade_count, mdd, normal_exit_count, eod_exit_count, overnight_count.
기존 TOP5의 TOP50 포함 여부와 정책별 unavailable 사유도 출력.

## 검증 / 남은 작업

- 47 PASS / 0 SKIP / 0 FAIL (로컬 PostgreSQL 포함).
- 100원 매수/110원 매도: gross=10, buy_fee=0.014052700,
  sell_fee=0.015457970, sell_tax=0.220, net=9.750489330.
- 기존 ResearchCostPolicy → for_stock → CompleteReplay 청산 비용 적용 확인.
- 세금 차감 후 다음 정수수량 감소/자본·MDD 변화, 신호·가격·lifecycle 불변 확인.
- 동일 결과 UNCHANGED / 변경 VERSION immutable 충돌 / REGULAR 단독 가용 / HTTP API 검증.
- 실제 운영 backfill, 실제 TOP50/TOP5, 실제 extended 부족 범위는 선행조건 해결 후 확인 필요.
- 기존 8천만원 연구결과 재산정은 **다음 단계**. 이번에 UPDATE/DELETE/재작성하지 않음.
