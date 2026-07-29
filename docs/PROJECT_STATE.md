# 프로젝트 현재 상태

## 현재 목표

KIS API RAW Collector의 반환 데이터를 PostgreSQL/TimescaleDB RAW 테이블에 안전하게 저장하는 기반을 완성한다.

## 현재 단계

토큰 로컬 캐시, ConnectionPool, RAW Repository, RawIngestionService 및 Ubuntu용 TimescaleDB 테스트 환경 구성을 완료했다. Ubuntu ARM64·Python 3.10·TimescaleDB 2.28.3/PostgreSQL 16에서 실제 통합 테스트를 통과했다.

주식·ETF 6개 상품의 KRX 과거 1분봉 백필을 테스트 DB에서 완료했다. `job_id=2`는 2026-05-27부터 2026-07-28까지 258개 세그먼트를 모두 완료했으며, 96,927행을 중복 없이 저장했다.

## 현재 이슈

- 통합 테스트는 `DB_INTEGRATION_TEST=1`과 이름에 `test`가 포함된 별도 DB에서만 실행된다.

## 다음 작업

- 최근월물 자동 선정과 만기 전환 방식을 별도 설계한다.
- 공식 지수선물 마스터 파일 자동 갱신 방식을 별도 설계한다.
## 백필 현재 상태

- 주식·ETF KRX 과거 1분봉 백필 기반을 추가했다.
- 실제 KIS `FHKST03010230` 호출에서 120행, 최신순, 행별 원문 보존을 확인했다.
- `job_id=2` 전체 백필은 996회 API 호출, 96,927행 조회·저장, 실패 0건으로 완료했다.
- 최종 DB 검증은 `scripts/backfill/verify_stock_minute_backfill.sh <job_id>`로 별도 실행한다.
- 선물 과거 백필은 계약·롤오버 목록 확정 전까지 보류한다.
