# FLOW V3 LIVE operation / Route 일반화

구현·로컬 검증용 변경이다. 운영 migration, 배포, 서비스 재시작, SEND 설정 변경,
TOP5 등록 및 실제 주문은 이 작업에서 수행하지 않는다.

## 계약

- 전략/PAPER 정의는 그대로 둔다. LIVE 실행상품은 operation에 별도로 저장한다.
- 본주(STOCK)의 내부 Route 코드는 `UNDERLYING`이다.
- 000660 LONG: UNDERLYING=000660, LEVERAGE=0193T0.
- 000660 SHORT: INVERSE=0197X0.
- 005930 LONG: UNDERLYING=005930, LEVERAGE=0193W0.
- 005930 SHORT: INVERSE=0193L0. 본주 SHORT는 지원하지 않는다.
- 현재 같은 strategy/route는 하나만 허용한다. 다른 route는 동시에 허용한다.
- 신규 operation의 초기자본은 사용자 입력 승인금액이다. 종목·TOP5·600만원을
  자본 상수로 사용하지 않는다. 기존 자본을 덮어쓰는 API는 제공하지 않는다.
- `current_capital = initial_capital + realized_net`, 수량은
  `floor(current_capital / reference_price)`이다. OPEN 존재는 신규 ENTRY 차단 조건이 아니다.
- intent 식별자는 operation+entry_event_key다. order는 intent, fill은 order/trade,
  lot과 settlement는 operation으로 소유권을 이어간다. FK는 다른 operation의
  lot/intent/trade/settlement 연결을 거부한다.
- 신규 ENTRY 중지/operation 종료 후에도 기존 lot의 청산·정산 책임은 유지한다.
  기존 lot은 종료된 operation의 자본으로 정산되며 새 epoch의 자본에 합산하지 않는다.
- 신규 등록은 ENTRY 중지 상태다. 시작/재개 시 resume 시각 이전 신호는 재생하지 않는다.
  기존 activated_at / stale / 15:18 cutoff / 15:19 실행 계약도 유지한다.

## 실제 계좌 주문가능 검사 — 최신 사용자 계약

UNDERLYING / LEVERAGE / INVERSE 모두 기존 `KISBrokerAvailableCashLookup`를 사용한다.
각 operation의 승인자본은 실제 계좌현금이 아니며, 여러 operation 자본을 합산해서
계좌 주문가능금액으로 사용하지 않는다.

- intent 준비 및 POST 직전에 실행상품별 KIS 주문가능금액을 조회한다.
- 금액 부족은 `BLOCKED / KIS_ORDERABLE_CASH_INSUFFICIENT`로 기록한다.
- 조회 실패/금액 누락은 fail-closed다. 이후 현금이 회복돼도 같은 ENTRY를 재처리하지 않는다.
- 실제 KIS 거절 응답은 REJECTED, 불명확한 응답/timeout은 UNKNOWN으로 보존한다.
  자동 재전송하지 않는다.
- LIVE에 별도의 30M 기본예탁금 계산이나 ETP 무조건 차단은 없다.
  상품 규제의 최종 판정은 KIS 조회/주문응답에 따른다.
- PAPER V0.7/V0.8의 30M / D+2 연구 계산은 변경하지 않았다.
- 기존 ENV+DB 전역 승인, operation 승인, 상품/수량 검증 및 durable claim을 유지한다.
  FLOW 계좌 단위 advisory lock 안에서 최신 현금 조회→POST를 직렬화한다.
  다른 시스템의 동시 주문이나 가격변동까지 로컬 자본으로 보장하지 않으며 KIS 응답이 최종이다.
- 기존 57 READY_NO_SEND의 send_enabled=false는 변경/소급 활성화하지 않는다.

## Migration / rollback

Forward: `database/migrations/20260913_flow_v3_live_routes.sql`

기존 capital.operation_id를 사용해 소유권 메타데이터만 연결한다. 기존 operation,
capital 금액, lifecycle key, 주문상태·번호·시도횟수, trade/fill/PAPER 값은 보존한다.
소유권이 불분명하면 전체 transaction을 중단한다. 신규 운용건을 생성하지 않는다.
원자적으로 적용되며 동일 migration 재실행은 no-op이다.

Rollback: `database/migrations/20260913_flow_v3_live_routes_down.sql`

새 USER_APPROVED operation을 등록하기 전, ENTRY 상태/resume 경계 변경 전만 허용한다.
신규 route 이력이 있으면 rollback을 거부한다. 이력을 지워서 rollback하지 않는다.
그 경우 forward repair가 필요하다. 구버전 코드와 새 schema를 섞어 실행하지 않는다.
운영 적용은 별도 승인 후 worker가 정지된 유지보수 구간에서 수행해야 한다.

READ ONLY 비교 SQL: `scripts/ops/verify_flow_v3_live_routes_readonly.sql`

첫 블록은 migration 전/후 원본 컬럼 fingerprint 비교용, 둘째 블록은 적용 후
operation ownership / duplicate / invariant 확인용이다. 큰 PAPER/RAW를 재계산하지 않는다.

## 운영자 CLI (이번 작업에서는 실행하지 않음)

기존 `scripts/admin/prepare_flow_v3_live_candidates.py`를 일반화했다.
아래는 **별도 운영 반영 승인 후 사용할 예시**이며 특정 후보 자동등록 기능이 아니다.

```bash
venv/bin/python scripts/admin/prepare_flow_v3_live_candidates.py strategies --search FV300
venv/bin/python scripts/admin/prepare_flow_v3_live_candidates.py list
venv/bin/python scripts/admin/prepare_flow_v3_live_candidates.py register \
  --strategy FV3008209 --route UNDERLYING --capital 6000000 \
  --approval-reference USER_APPROVAL_REFERENCE
# register가 반환한 operation_id를 사용한다.
venv/bin/python scripts/admin/prepare_flow_v3_live_candidates.py start-entry --operation OPERATION_ID
venv/bin/python scripts/admin/prepare_flow_v3_live_candidates.py stop-entry --operation OPERATION_ID
venv/bin/python scripts/admin/prepare_flow_v3_live_candidates.py close --operation OPERATION_ID
```

`register`는 ENTRY=false, 전역 SEND 불변이다. `start-entry`는 전역 SEND가 이미 Y/Y라면
새 자연신호 주문으로 이어질 수 있으므로 실제 승인된 operation에만 실행한다.
자본 변경은 close 후 새 승인금액으로 register하는 새 epoch 방식이다.
구 operation의 OPEN 청산 및 자본 이력은 계속 보존된다.
기존 runtime `--activate-approved` 일괄 초기화는 거부한다.

Dashboard 조회 호환성: strategy에 여러 capital이 있으면 `live_capitals` 배열로 반환한다.
단일 `live_capital`에 임의 route를 넣거나 자본을 합산하지 않는다. 신규 route용 화면 작업은 별도다.

## 로컬 검증

운영 환경변수/계좌를 읽지 않는 테스트다. `FLOW_ROUTE_TEST_DSN`은
`host=127.0.0.1 port=55491 dbname=flow_route_test` 전용의 비어 있는 테스트 DB만 허용한다.
기존 DB를 테스트 코드가 삭제하지 않는다. psycopg/psycopg_pool이 필요하다.

```bash
FLOW_ROUTE_TEST_DSN='host=127.0.0.1 port=55491 dbname=flow_route_test user=postgres' \
python -m unittest test.test_flow_v3_live_contract test.test_flow_v3_send_authorization \
  test.test_flow_v3_live_routes test.test_flow_v3_preparation test.test_flow_v3_runtime \
  test.test_flow_v3_accounting test.test_flow_v3_contract_correction \
  test.test_flow_v3_dashboard_detail -v
git diff --check
```

DB 테스트는 구 schema fixture + 실제 기존 migration을 사용한다. 기존 15개/57건 보존,
forward→rollback→forward, 신규 route fan-out/FK/자본 손익/종료 후 EXIT,
부분체결 5→3 / 취소 경합 5→4 / 전량 / 0체결, 중복/재시작,
현금 부족·KIS 거절 후 재처리 금지, 가짜 HTTP transport를 검증한다.
운영 migration 실제 적용, 실계좌 주문가능금액 응답 및 자연체결 E2E는 수행하지 않았다.

최종 로컬 결과: 70개 중 64 PASS / 6 SKIP. SKIP은 별도 opt-in인 기존
Dashboard 운영 DB 조회 5개와 브라우저 1개이며, 위 로컬 PostgreSQL lifecycle 테스트는 PASS다.

## 변경 파일 목록

- `src/flow_v3/live_contract.py`: 6개 상품/Route 계약.
- `src/flow_v3/live_operations.py`: 명시적 승인자본·운용기간 관리.
- `src/flow_v3/live_repository.py`: operation별 원장·복리·ENTRY/EXIT 소유권.
- `src/flow_v3/live_preparation.py`: operation별 비전송 관찰 이력.
- `src/flow_v3/live_transport.py`: operation 승인 및 최종 계좌 검사.
- `src/flow_v3/live_broker.py`: operation 상품별 quote 대상.
- `src/flow_v3/preorder.py`: 모든 Route 공통 KIS 주문가능금액 검사.
- `src/service/flow_v3_dashboard_service.py`: 다중 자본 조회 호환성.
- `scripts/admin/prepare_flow_v3_live_candidates.py`: 일반 운용등록 CLI.
- `scripts/ops/flow_v3_send_approval.py`: 고정 15개 대신 승인 operation 검증.
- `scripts/runtime/run_flow_v3_live.py`: 기존 worker에 cash/quote 연결.
- `scripts/research/verify_flow_v3_live_temp.py`: 구 단일소유권 검증기의 실행 중단 안내.
- `database/migrations/20260913_flow_v3_live_routes.sql`: forward migration.
- `database/migrations/20260913_flow_v3_live_routes_down.sql`: 안전조건부 rollback.
- `scripts/ops/verify_flow_v3_live_routes_readonly.sql`: 전후 비교/무결성 SQL.
- `test/test_flow_v3_live_contract.py`: 기존 계약 회귀.
- `test/test_flow_v3_send_authorization.py`: 승인·최종현금 검사 회귀.
- `test/test_flow_v3_live_routes.py`: 순수/격리 PostgreSQL 통합검증.
- `test/flow_v3_legacy_fixture.py`: 기존 15개 테스트 데이터.
- `test/fixtures/flow_v3_routes_legacy.sql`: 격리 DB 기본 fixture.
- `docs/FLOW_V3_LIVE_ROUTES.md`: 본 문서.
