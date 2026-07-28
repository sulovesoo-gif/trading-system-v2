# Codex 전달문 - KIS RAW Collector 및 DB 정리

프로젝트 문서 `AI_GUIDE.md`, `PRINCIPLES.md`, `API_LIST_REVISED.md`를 먼저 읽고 작업한다.

## 작업 목표

KIS Open API 기반 RAW Collector와 SQL 스키마를 프로젝트 규칙에 맞게 정리한다.

## 절대 규칙

- 프로젝트 루트 밖은 수정하지 않는다.
- Collector 1개 = API 1개.
- Collector는 호출, 응답 검증, 타입 변환, RAW 저장만 수행한다.
- Feature, Trend, Event 계산을 넣지 않는다.
- 모든 시간은 KST(Asia/Seoul) 기준이다.
- API 응답 필드는 가능한 한 100% 보존한다.
- 기존 파일을 임의 삭제하지 말고 변경 전 영향 범위를 확인한다.
- 문서와 실제 API 응답이 충돌하면 구현을 추측하지 말고 TODO로 남긴다.

## 확정 API

`API_LIST_REVISED.md`의 8개 API를 기준으로 구현한다.

## SQL 작업

1. `10_raw_program.sql`
   - `API_LIST_REVISED.md`의 `raw_program` 컬럼명으로 정리한다.
   - `previous_day_difference`, `previous_day_difference_sign`을 포함한다.

2. 기존 `11_raw_market_flow.sql`
   - 현물/선물 컬럼을 한 행에 병합한 구조를 사용하지 않는다.
   - `raw_market_investor`로 재설계한다.
   - `market_code`로 KOSPI, KOSDAQ, KOSPI200_FUTURES 등을 구분한다.
   - 투자자별 매도/매수/순매수의 거래량과 거래대금 6개 필드를 모두 저장한다.
   - `fund`를 임의로 `pension`으로 명명하지 않는다.

3. 기존 `12_raw_price.sql`
   - `raw_stock_quote`와 `raw_stock_execution`으로 책임을 분리한다.

4. 기존 `13_raw_futures.sql`
   - `raw_futures_quote`로 정리한다.
   - `open_interest_change`, `basis`, `theoretical_price`, `market_basis`, `expiration_date`, `days_to_expiration`을 포함한다.

5. 신규 SQL
   - `raw_stock_minute`
   - `raw_stock_daily`
   - `raw_futures_minute`

6. 모든 시계열 테이블
   - TimescaleDB hypertable 유지
   - PK에는 시간 + source + market + cycle + instrument key를 포함
   - 조회 패턴에 맞는 `(instrument_code, time)` 인덱스 생성
   - 컬럼 COMMENT는 한국어로 작성

## Python 작업

- `kis_auth.py`: 토큰 만료시간을 관리하고 만료 전 재발급하도록 개선한다.
- `kis_client.py`: `raise_for_status()`, KIS `rt_cd` 검증, timeout, 명확한 예외를 구현한다.
- API별 Collector를 `collector/raw/<domain>/` 구조로 분리한다.
- 빈 문자열, 공백, 부호 있는 숫자를 안전하게 변환하는 공통 변환 함수를 만든다.
- `snapshot_time`/`bar_time`은 API의 영업일자와 시간을 결합해 KST로 만든다.
- API에 날짜가 없으면 KST 현재 영업일을 사용하되 해당 가정을 코드 주석과 문서에 명시한다.
- 원본 응답 보존을 위해 각 테이블에 `raw_payload JSONB`를 추가하는 방안을 적용한다. 기존 명시 컬럼과 함께 저장한다.

## Collector 목록

- `program_collector.py`
- `market_investor_collector.py`
- `stock_quote_collector.py`
- `stock_execution_collector.py`
- `stock_minute_collector.py`
- `stock_daily_collector.py`
- `futures_quote_collector.py`
- `futures_minute_collector.py`

## 테스트

- API별 매핑 테스트
- 빈 문자열/음수/소수 변환 테스트
- KST 시간 결합 테스트
- `rt_cd != "0"` 오류 테스트
- DB INSERT 컬럼 일치 테스트

## 완료 결과

- 변경 파일 목록
- 변경 이유
- 실행 방법
- 테스트 결과
- 아직 실제 응답 확인이 필요한 필드/TODO

을 짧게 보고한다.
