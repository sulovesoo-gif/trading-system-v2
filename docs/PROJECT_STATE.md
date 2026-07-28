# 프로젝트 현재 상태

## 현재 목표

KIS API RAW Collector의 반환 데이터를 PostgreSQL/TimescaleDB RAW 테이블에 안전하게 저장하는 기반을 완성한다.

## 현재 단계

토큰 로컬 캐시, ConnectionPool, RAW Repository, RawIngestionService 및 Ubuntu용 TimescaleDB 테스트 환경 구성을 완료했다.

## 현재 이슈

- 실제 Ubuntu 서버와 TimescaleDB 테스트 DB에서의 컨테이너 기동 및 통합 테스트는 아직 실행하지 않았다.
- 통합 테스트는 `DB_INTEGRATION_TEST=1`과 이름에 `test`가 포함된 별도 DB에서만 실행된다.

## 다음 작업

- Ubuntu 서버에서 TimescaleDB 테스트 컨테이너를 기동하고 RAW Repository 통합 테스트를 실행한다.
- 최근월물 자동 선정과 만기 전환 방식을 별도 설계한다.
- 공식 지수선물 마스터 파일 자동 갱신 방식을 별도 설계한다.