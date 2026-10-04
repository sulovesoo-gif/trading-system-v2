# FIRST_RISE J V2.0 구현·배포 안내 (2026-10-04)

## 검증 상태
- 기준 HEAD: 96adcfaea58a013b830863eda1fe89dd73a2f192, codex/minute-ma-v181.
- 기존 미커밋 J/epoch/누적 checkpoint/취소·recovery/비용 변경을 보존하여 확장.
- 로컬 범위 검증: 179 PASS, 228 SKIP, 12 subtests PASS. SKIP은 PostgreSQL 환경조건 등이며 PASS로 계산하지 않음.
- PostgreSQL TEMP: 68개 대상. 67개 PASS 후 SQL 실행 harness 오류 1개를 정정하여 단독 재검증 PASS. HTTP mock 차단, 운영 테이블 DML 없음.
- 배포 전 전체 회귀 1회: 882 PASS / 2 FAIL / 281 SKIP / 157 subtests PASS.
- 기존 FAIL: V1HistoricalTest.test_research_schema_is_provenance_isolated (CSS 기대3100, 실제3215); RuntimeDependencyTest.test_runtime_requirements_are_limited_to_raw_runtime_packages (websockets 기대목록 누락).
- 위 2개 파일/관련 구현은 HEAD 대비 변경 없음. unrelated baseline으로 수정하지 않음.
- migration manifest 기대값은 기존 39번 DDL을 포함하도록 테스트만 정정.
- 실제 broker 주문/취소 POST 검증 없음. 최초 actual submit 승인 전 activation 생성/실행서비스 시작 금지.

## 구조 및 실제 반영 위치
- runtime.py → j_runtime.py → j_signal.py → V2Strategy. FLOW 프로세스에는 시장신호만, 별도 run_first_rise_live.py에 actual 실행.
- v2_capacity.V2Strategy: previous_close 무의존, 눌림1.6~10%, rest6분. legacy FirstRiseBreakoutStrategy는 역사 재현용 보존.
- minute_source: 기존 FHKST03010200 bootstrap/incremental/catch-up/완료봉/RAW 경로 유지. V2 previous-close 검증 비활성. 잘못된 금액은 NULL+원본 evidence로 남기고 실제 sizing 차단, OHLC 신호/EXIT 계속.
- J FIRST≤10:00, STOP 후 SECOND≥10:00, THIRD 금지·bootstrap no-replay 유지.
- EXIT STOP/BOOK_TENKAN/SESSION_CLOSE 및 가격·비용 공식 유지.
- v2_capacity.unbounded_slot: START + max(0,floor(epoch PnL/STEP))*STEP. 기존 DB generated capped common_slot은 역사호환용이며 V2 sizing 원천이 아님.
- j_execution.plan_entry → capacity_sizing: min(unbounded slot, broker cash - unacknowledged reservation, 최근5완료봉금액×participation%).
- j_live_repository: formula_version으로 V2 경로 선택, intent/binding/request detail 및 strategy_capital_before에 V2 domain 계산값 저장.
- 기존 attr7은 삭제/과거수정하지 않음. runtime config의 역사적 양수/MAX≥START 형식 검증은 유지; 자동 복리 1억원 초과 sizing은 TEMP request로 검증.
- recent_liquidity: 신호봉 포함5봉, 누적 차분, 09:00 최초값, 선행봉/연속성 확인. 진행봉 미사용. 오류는 BUY만 fail-closed.
- FIRST_RISE_CAPACITY/DEFAULT attrs: 10,10000000,30,100,3,5,10. 일일 DB snapshot으로 재시작/장중 변경 혼합 방지.
- v2_capacity_repository.shadow_entry/exit: 시장신호별 fixed10M 별도원장, K_MODE PAPER를 덮어쓰지 않음. 실제 미체결도 독립EXIT까지 계산.
- CapacityMonitor: LIVE trade와 동일 market_signal Shadow를 1:1 비교. 실제 매수금액+매수비용 대비 provisional/final 순수익률. 기존 cost/epoch를 관찰만 하므로 비용 중복반영 없음.
- return_gap_bp=(live return-shadow return)*10000; 30/100 CLOSED 거래 동일가중. Shadow 평균>0에서만 degradation 비율 계산.
- warning3/5/10: durable severity/revision, 상향 또는 회복후 재상향만 이벤트. 알림 attempted claim 뒤 외부 전송, UNKNOWN은 재전송 안 함(중복방지; 전달보장 아님).
- runner에서 기존 ntfy 우선, 미설정 시 기존 email, 모두 없으면 DB/log만. 경고/알림 실패는 주문 gate가 아님.
- 실제 체결시각은 누적 KIS 조회 관측시각으로만 알 수 있음. first_rise_v2_order_observation의 시간은 관측 latency 포함, 개별 실체결시각이라고 주장하지 않음.
- aggregate SELL은 기존 FIFO trade allocation/epoch ownership을 유지. LIVE 실제 수익률은 각 trade 귀속량/금액으로 계산.
- FIRST의 잔량이 SECOND에서 팔리는 경우 FIRST 독립 EXIT 모델과 실제 청산성과 차이가 비교에 포함됨.
- Minute MA 전략/자본/09:03/09:10, FLOW/RAW collector 소스와 subscriptions 미변경.
- 공용 비용 변경은 FIRST_RISE family 및 multi-trade allocation ownership 확장만; DAILY/MINUTE/FLOW 공식 불변.

## 읽기 전용 조회
database/verification/first_rise_v2_capacity_readonly.sql:
- 실제 참여율0~1/1~2/2~5/5~7.5/7.5~10 및 cap-hit cohort.
- 동일가중 수익률/훼손, 평균·중앙 gap, entry/exit slippage, 부분체결/관측완료시간.
- STOP_ENTRY_BREAK 별도 그룹.
- durable warning/alert 상태.
- 실제 fill/외부 알림 수신은 미검증; 자연 첫 거래에서 검증.

## Migration 순서 (기존 운영 J 스키마가 없음을 SELECT 확인)
1. 20261002_first_rise_completed_minute_raw.sql
2. 20261002_first_rise_j_market.sql
3. 20261002_first_rise_j_capital_epoch.sql
4. 20261002_first_rise_j_live_cost.sql
5. 20261003_first_rise_j_execution.sql
6. 20261004_first_rise_v2_capacity.sql
7. 20261004_first_rise_v2_capacity_config.sql

새 테이블/FK/UNIQUE와 FIRST_RISE 비용 family 확장. 기존 거래 재작성 없음.
common_code는 ON CONFLICT DO NOTHING. 기존 FIRST_RISE_RUNTIME 값 덮어쓰지 않음.
activation은 migration에서 생성하지 않음.
보호 대상 row count+내용 digest를 migration 전후 비교, 다르면 transaction rollback.

## 서비스/승인 순서
1. 대상 파일 commit/push, 운영 branch HEAD 확인. 운영 docker-compose 등 무관 변경 보존.
2. 위 migration 원자적 적용, 기존 데이터 불변 확인.
3. 코드 fast-forward 배포.
4. 시장신호가 포함된 trading-flow-raw-collector.service만 필요 재시작. 다른 기존서비스 재시작 금지.
5. 별도 trading-first-rise-live.service는 설치/준비만; 최초 actual submit 승인 전 start/enable 및 activation 생성 안 함.
6. READ ONLY: collector active, config 일일값, traceback, FIRST_RISE 동적WS 추가0 확인.
7. 최초 submit 승인 후 그 시점 durable effective_from을 설정하여 과거신호 재주문 차단. 실제 테스트 주문 만들지 않음.

## 첫 자연 V2 거래 확인
formula/capacity/shadow version, market signal/trade/epoch identity, 5봉timestamp/각차분,
unbounded slot/compound reference, broker cash/reservation, target qty/cash,
실제 checkpoint ownership, actual 참여율, Shadow 독립 EXIT,
provisional→final return/cost delta, old epoch 손익 분리, warning 이벤트 중복 없음.
실제 신호가 없으면 거래0은 정상이며 PASS를 대신 주장하지 않음.

## Rollback
- 실제 actual 포지션/주문이 없는 경우만 검증된 이전 코드 배포를 검토.
- 실제 OPEN/PENDING이 있으면 FIRST/SECOND cancel/recovery 및 EXIT 책임을 유지한 채 별도 대응 승인 필요.
- additive 테이블/RAW/Shadow/epoch/order 이력 DROP/DELETE 금지.
- 비용 CHECK를 FIRST_RISE 데이터가 존재하는 상태에서 구 범위로 축소 금지.
- activation을 과거로 되돌리거나 초기화하지 않음.
- 새 DB의 일일 snapshot/과거 realized PnL을 재계산하지 않음.

## 변경 파일 전체 목록과 역할
- `src/broker/shared_cost_allocation.py`: FIRST_RISE 공용 비용 ownership 확장
- `src/broker/shared_cost_repository.py`: FIRST_RISE 공용 비용 ownership 확장
- `src/first_rise_breakout/config.py`: 완료분봉·runtime·기존계약 호환
- `src/first_rise_breakout/minute_source.py`: 완료분봉·runtime·기존계약 호환
- `src/first_rise_breakout/repository.py`: 완료분봉·runtime·기존계약 호환
- `src/first_rise_breakout/runtime.py`: 완료분봉·runtime·기존계약 호환
- `src/first_rise_breakout/strategy.py`: 완료분봉·runtime·기존계약 호환
- `test/test_execution_migration_runner.py`: 회귀/TEMP/E2E 계약 검증
- `test/test_first_rise_breakout.py`: 회귀/TEMP/E2E 계약 검증
- `test/test_first_rise_runtime_config_cache.py`: 회귀/TEMP/E2E 계약 검증
- `database/migrations/20261002_first_rise_completed_minute_raw.sql`: 독립 additive 영속화/제약
- `database/migrations/20261002_first_rise_j_capital_epoch.sql`: 독립 additive 영속화/제약
- `database/migrations/20261002_first_rise_j_live_cost.sql`: 독립 additive 영속화/제약
- `database/migrations/20261002_first_rise_j_market.sql`: 독립 additive 영속화/제약
- `database/migrations/20261003_first_rise_j_execution.sql`: 독립 additive 영속화/제약
- `database/migrations/20261004_first_rise_v2_capacity.sql`: 독립 additive 영속화/제약
- `database/migrations/20261004_first_rise_v2_capacity_config.sql`: 독립 additive 영속화/제약
- `scripts/runtime/run_first_rise_live.py`: 기존 broker·비용·알림 배선
- `src/first_rise_breakout/j_cancel.py`: 기존 승인 J lifecycle/실행·복원/epoch·비용
- `src/first_rise_breakout/j_capital.py`: 기존 승인 J lifecycle/실행·복원/epoch·비용
- `src/first_rise_breakout/j_cost.py`: 기존 승인 J lifecycle/실행·복원/epoch·비용
- `src/first_rise_breakout/j_epoch.py`: 기존 승인 J lifecycle/실행·복원/epoch·비용
- `src/first_rise_breakout/j_execution.py`: 기존 승인 J lifecycle/실행·복원/epoch·비용
- `src/first_rise_breakout/j_live_repository.py`: 기존 승인 J lifecycle/실행·복원/epoch·비용
- `src/first_rise_breakout/j_live_runtime.py`: 기존 승인 J lifecycle/실행·복원/epoch·비용
- `src/first_rise_breakout/j_raw_tracking.py`: 기존 승인 J lifecycle/실행·복원/epoch·비용
- `src/first_rise_breakout/j_recovery.py`: 기존 승인 J lifecycle/실행·복원/epoch·비용
- `src/first_rise_breakout/j_repository.py`: 기존 승인 J lifecycle/실행·복원/epoch·비용
- `src/first_rise_breakout/j_runtime.py`: 기존 승인 J lifecycle/실행·복원/epoch·비용
- `src/first_rise_breakout/j_signal.py`: 기존 승인 J lifecycle/실행·복원/epoch·비용
- `src/first_rise_breakout/j_submit.py`: 기존 승인 J lifecycle/실행·복원/epoch·비용
- `src/first_rise_breakout/minute_raw_repository.py`: 완료분봉·runtime·기존계약 호환
- `src/first_rise_breakout/raw_replay.py`: 완료분봉·runtime·기존계약 호환
- `src/first_rise_breakout/v2_capacity.py`: V2 sizing/Shadow/관찰·경고
- `src/first_rise_breakout/v2_capacity_repository.py`: V2 sizing/Shadow/관찰·경고
- `systemd/trading-first-rise-live.service`: 독립 실제주문 프로세스 정의
- `test/test_first_rise_j.py`: 회귀/TEMP/E2E 계약 검증
- `test/test_first_rise_j_cancel_contract.py`: 회귀/TEMP/E2E 계약 검증
- `test/test_first_rise_j_cost.py`: 회귀/TEMP/E2E 계약 검증
- `test/test_first_rise_j_epoch.py`: 회귀/TEMP/E2E 계약 검증
- `test/test_first_rise_j_execution.py`: 회귀/TEMP/E2E 계약 검증
- `test/test_first_rise_j_exit_continuity.py`: 회귀/TEMP/E2E 계약 검증
- `test/test_first_rise_j_runtime.py`: 회귀/TEMP/E2E 계약 검증
- `test/test_first_rise_j_runtime_pg.py`: 회귀/TEMP/E2E 계약 검증
- `test/test_first_rise_j_submit.py`: 회귀/TEMP/E2E 계약 검증
- `test/test_first_rise_j_wiring_e2e.py`: 회귀/TEMP/E2E 계약 검증
- `test/test_first_rise_minute_raw.py`: 회귀/TEMP/E2E 계약 검증
- `test/test_first_rise_raw_replay.py`: 회귀/TEMP/E2E 계약 검증
- `test/test_first_rise_v2_capacity.py`: 회귀/TEMP/E2E 계약 검증
- `test/test_first_rise_v2_postgres.py`: 회귀/TEMP/E2E 계약 검증
- `database/verification/first_rise_v2_capacity_readonly.sql`: 참여율·STOP 읽기 전용 집계
