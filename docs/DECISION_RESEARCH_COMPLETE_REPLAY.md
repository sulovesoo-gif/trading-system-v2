# 연구용 COMPLETE 백필·재생 결정

- 연구 실행은 `research_*` 테이블에만 저장한다. 공식 RAW, 실시간 다중 MA,
  기존 SMA 신호·알림 및 주문 경로는 변경하지 않는다.
- COMPLETE feature는 저장된 `raw_stock_minute`의 공식 KIS 1분 종가만 사용한다.
  5초 스냅샷을 완료봉으로 대체하거나 보간하지 않는다.
- `SIGNAL_1`/`SIGNAL_2`/`SIGNAL_3`은 `src.analysis.event.multi_ma_event.detect_signals`
  하나를 재사용한다. 연구용으로 별도 신호 공식을 만들지 않는다.
- MA10_CONFIRM은 동일 세션의 직전 연속 COMPLETE MA10 방향으로만 확인하며,
  분 공백·세션 전환을 넘겨 pending/MA 상태를 유지하지 않는다.
- 신호는 신호 원천 종목, 가격·수량·손익은 거래 대상 종목의 **동일 시각** COMPLETE
  가격으로만 계산한다. 그 가격이 없으면 source 가격·직전값·다음값으로 대체하지 않는다.
- 기준 원금은 조합별 10,000,000원이다. 수량은 `floor(10,000,000 / entry_price)`이며
  기간 원금 수익률의 분모는 거래 횟수만큼 누적하지 않는다.
- STOCK attr10은 현재 `PROGRAM_COLLECT_YN`으로 사용 중임을 전수 확인했다.
  본 연구 기능은 STOCK attr를 변경하거나 새 의미로 재사용하지 않는다.
