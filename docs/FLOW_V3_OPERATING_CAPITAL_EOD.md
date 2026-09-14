# FLOW LIVE 운용금액 · EOD · RAW · Dashboard (2026-09-14)

로컬 구현용 문서. 이번 작업에서 운영 DB, SEND, 서비스는 변경하지 않는다.

## 운용금액

```bash
venv/bin/python scripts/admin/prepare_flow_v3_live_candidates.py set-capital \
  --strategy FV3008049 --route UNDERLYING --capital 6000000 --reference USER_APPROVAL_UNIQUE_REFERENCE
venv/bin/python scripts/admin/prepare_flow_v3_live_candidates.py set-capital \
  --strategy FV3008084 --route LEVERAGE --capital 0 --reference USER_PAUSE_UNIQUE_REFERENCE
```

이 명령은 **다음 별도 운영 단계에서만** 사용한다. 예시를 배포 절차에 자동 포함하지 않는다.
양수 신규 등록은 ENTRY 허용; 전역 SEND Y/Y이면 이후 자연 신규 신호가 실제 주문으로 이어질 수 있다.
0원은 allocated_amount=0 / entry_enabled=false일 뿐, 기존 initial/current capital, NET,
lot/order/fill/settlement를 변경하지 않는다. 기존 SELL 책임은 유지한다.
0→양수 또는 다른 양수로 변경하면 이전 operation을 종료하고 새 승인금액의 capital epoch를 만든다.
이전 epoch의 자본/실현손익은 새 epoch로 이전하지 않는다. 이전 OPEN 청산손익은 이전 epoch에 귀속된다.
같은 양수 금액은 current_capital을 초기화하지 않는다. 저수준 pause 상태라면 현재 epoch를 재개한다.
재개/신규 epoch의 entry_resume_at 이후 신호만 허용한다. 동일 reference 재실행은 audit 결과만 반환하고
resume 시각을 움직이거나 다시 활성화하지 않는다. 다른 의도는 새 reference를 사용한다.

Migration은 `flow_v3_live_capital_change` 감사 테이블 하나만 추가한다. operation_id,
replacement_operation_id, 전후 승인금액, 변경 전 current capital/realized NET, 시각, 사유/reference를 보존한다.
기존 테이블 DML, 15개 ETP 0원 변경, TOP5 600만원 변경을 포함하지 않는다.

## EOD 실행

15:18은 **완료 소스봉 timestamp**다. 벽시계 15:18의 진행 중 1분봉을 미리 확정하지 않는다.
15:18 봉과 cursor가 완료되는 15:19 이후 signal_time=15:18,
execution_not_before=15:19로 생성한다. 15:20 hard boundary는 유지한다.

15:18~15:20 worker는 1초 간격으로 EOD 우선 pass를 수행한다:
release 생성 → 필요한 broker history/취소가능 잔량 → fresh quote → SELL 준비 → transport.
동일 pass 내 2회로 취소 ACK 이후 최종 history를 다시 확인한다. 일반 과거주문 bulk polling은
이 구간에서 뒤로 미루고, 일반 BUY 준비/전송은 회차당 1건으로 제한해 다음 SELL pass를 막지 않는다.
cancel ACK만으로 잔량을 확정하지 않는다. 실제 최종 ENTRY 체결수량만 SELL 대상으로 삼는다.
POST 직전 계좌 잠금/현금 조회 이후 실제 DB clock으로 시간과 quote 신선도를 다시 확인한다.
claim은 먼저 commit하며 불확실한 주문을 재전송하지 않는다. dual gate는 그대로다.

DB/broker 장애나 15:18 state 지연이 계속되면 15:20 이전 체결/접수를 보장할 수는 없다.
이때 경계를 늘리거나 과거 BLOCKED/EOD_EXIT_UNFILLED/READY_NO_SEND를 rearm하지 않는다.
실제 자연신호 타이밍은 배포 후 별도 검증 대상이며 이 작업에서는 mock POST만 테스트한다.

## RAW / 화면

WebSocket liveness 09:00~20:00 + 명시적 60초 grace (20:01 미만).
recv timeout 1초, 누적 data silence 60초는 유지한다. 20:01은 연결 종료시각이 아니다.
12구독, source/raw payload, duplicate_flag 및 orderbook bucket upsert는 불변이다.
FLOW completed_minutes의 `<15:31`도 불변; 애프터마켓은 RAW 연구자료만이다.

Dashboard는 current LIVE operation에서 동적으로 전략을 선택한다.
전략당 PAPER 1행 + current UNDERLYING/LEVERAGE/INVERSE 순 LIVE 행이다.
종료 epoch는 원장에 보존하지만 current 화면에는 합산하지 않는다.
정렬은 PAPER 누적 성과 기준이며 기본 복리수익률↓/거래당순익↓/현재자본↓/전략ID↑.
LIVE 순익/완료수/승률은 실제 정산완료 기준, MDD는 확정 자본 곡선 기준(평가손익 MDD 아님).
비용 미확정 완료체결은 별도 정산 대기로 표시한다. 완료수0이면 거래당순익/승률 NULL.
8천만원 V0.8 Snapshot은 별도 연구/참고 영역이며 운영 자본과 합치지 않는다.

## 다음 운영 적용 순서 (이번에는 실행하지 않음)

1. 새 커밋을 별도 승인 후 push. Ubuntu 기존 checkout이 clean인지, 현재 unit/drop-in/SEND 값을 READ ONLY로 저장한다.
2. 기존 `trading-flow-v3-live-nosend.service`를 정상 정지하고 자식 종료 확인. 실제 OPEN/미체결은 삭제/복구하지 않는다.
3. 현재 LIVE/PAPER count/상태와 자본·lot·주문·정산을 READ ONLY로 보존 비교한다.
   `scripts/ops/verify_flow_v3_live_routes_readonly.sql` 및 새 검증 SQL의 첫 트랜잭션을 사용한다.
4. 승인 커밋으로 fast-forward 반영. 기존 DB 연결 절차로 아래 migration만 ON_ERROR_STOP 적용:
   `database/migrations/20260914_flow_v3_capital_change.sql`.
   기존 20260913 route migration이 이미 적용된 DB가 전제다. 오류 시 재시작/후속 DML 중단.
5. `scripts/ops/verify_flow_v3_operating_contract_readonly.sql` 전체 실행. 기존 금액/이력/57건,
   PAPER 상태별 수, invariant/중복/orphan과 SEND 값이 유지되고 신규 audit가 비어 있어야 한다.
6. 기존 unit 설정 그대로 LIVE worker를 시작한다. RAW liveness 및 Dashboard 반영에는 각각 기존
   `trading-flow-raw-collector.service`, `trading-multi-ma-dashboard.service` 재시작이 필요하다.
   shared WebSocket은 기존 구독을 그대로 복구하며 새 unit/drop-in을 설치하지 않는다.
   PAPER/accounting/Minute/Daily 코드는 불변이므로 재시작 대상이 아니다.
7. 서비스 active/log와 Dashboard API/current route/연구 Snapshot을 READ ONLY 확인한다.
   자연 EOD/애프터마켓 수신은 해당 시간 별도 관찰한다. 테스트 주문/신호 생성 금지.
8. 운용금액 변경은 배포와 분리된 사용자 의사결정이다. 이번 migration/배포만으로 금액을 바꾸지 않는다.

실패 시 기존 커밋으로 코드만 복귀할 수 있다. 감사 테이블과 기존 금융 원장은 삭제하지 않는다.
배포 후 새 epoch가 실제 생성됐다면 기존 route down migration으로 이력을 제거하지 않는다.

## 로컬 검증 결과

- 전체 unittest discovery: 587건 / PASS 566 / SKIP 18 / FAIL 3 / ERROR 0.
- FAIL 3은 수정 전 `659bce9`를 임시 디렉터리에 archive하여 동일 실패 재현:
  execution migration 목록의 기존 추가파일, Minute 화면의 기존 폭 문자열, runtime 의존성의 기존 websockets.
  이번 범위 밖이므로 해당 코드/테스트를 수정하지 않았다.
- localhost:55491 `flow_route_test` 격리 PostgreSQL migration/재적용/자본 epoch/0원 SELL/
  replay 방지/15:19 claim/15:20 거부/기존 57건 보존/부분체결/복리 검증 PASS.
- Dashboard 전체 JavaScript compile 및 실제 render 함수에서 PAPER 1+LIVE 3, rowspan,
  NULL 거래당순익, modal strategy identity 검증 PASS. 새 LIVE SQL은 격리 PostgreSQL에서 실행 PASS.
- RAW 가짜 WebSocket 16/19/20시 frame 수신·저장 및 반복 frame duplicate_flag 보존 PASS.
- SKIP에는 운영 DB/실서비스 브라우저 opt-in 및 다른 외부환경 테스트가 포함된다.
  운영 화면 육안검증, 실제 15:19 자연신호, KIS 애프터마켓 제공 여부는 실행하지 않았다.

## 변경 파일

- `database/migrations/20260914_flow_v3_capital_change.sql`
- `src/flow_v3/live_operations.py`
- `scripts/admin/prepare_flow_v3_live_candidates.py`
- `src/flow_v3/live_repository.py`
- `src/flow_v3/live_broker.py`
- `src/flow_v3/live_transport.py`
- `src/flow_v3/live_scheduler.py`
- `scripts/runtime/run_flow_v3_live.py`
- `src/flow_raw/collector.py`
- `src/service/flow_v3_dashboard_service.py`
- `reports/multi-ma/flow-v3.html`
- `scripts/ops/verify_flow_v3_operating_contract_readonly.sql`
- `test/test_flow_raw_collector.py`
- `test/test_flow_v3_live_contract.py`
- `test/test_flow_v3_live_routes.py`
- `test/test_flow_v3_preparation.py`
- `test/test_flow_v3_send_authorization.py`
- `test/test_flow_v3_operating_contract.py`
- `test/flow_v3_operating_fixture.py`
- `docs/FLOW_V3_OPERATING_CAPITAL_EOD.md`
