# 프로젝트 현재 상태

## 현재 목표

KIS API RAW Collector의 반환 데이터를 PostgreSQL/TimescaleDB RAW 테이블에 안전하게 저장하는 기반을 완성한다.

## 현재 단계

토큰 로컬 캐시, ConnectionPool, RAW Repository, RawIngestionService 및 Ubuntu용 TimescaleDB 테스트 환경 구성을 완료했다. Ubuntu ARM64·Python 3.10·TimescaleDB 2.28.3/PostgreSQL 16에서 실제 통합 테스트를 통과했다.

주식·ETF 6개 상품의 KRX 과거 1분봉 백필을 테스트 DB에서 완료했다. `job_id=2`는 2026-05-27부터 2026-07-28까지 258개 세그먼트를 모두 완료했으며, 96,927행을 중복 없이 저장했다.

## 현재 이슈

- 통합 테스트는 `DB_INTEGRATION_TEST=1`과 이름에 `test`가 포함된 별도 DB에서만 실행된다.

## 원격 테스트 환경

- Windows Codex는 OpenSSH `trading-v2` 별칭을 통해 Ubuntu ARM64 테스트 서버에 직접 접근할 수 있다.
- 원격 프로젝트 경로는 `/home/ubuntu/projects/trading-system-v2`이며, 테스트 DB `trading_system_v2_test`의 TimescaleDB 컨테이너는 healthy 상태다.
- Windows 로컬, `origin/main`, Ubuntu 원격 HEAD는 모두 `4833c1b17e5d24d983d838173de08f292595262d`로 동기화되어 있고, 로컬·원격 작업 트리는 clean 상태다.

## 다음 작업

- SK하이닉스 완료 1분봉 SMA5/SMA10 크로스 알림은 Analysis·Repository·ntfy 기본 알림 코드와 테스트를 구현했으며, Ubuntu 테스트 서버에서 독립 ntfy 연결 스모크를 수행했다. 테스트 DB 적용 및 본장 알림 전용 스모크 검증이 남아 있다.
- SMA 신호의 주 시계열은 `000660 / UN / INTEGRATED` 완료 1분봉으로 유지한다. 장후 KIS 응답에서 UN OHLC·체결량은 NX와 같고 누적 거래대금은 별도 값으로 확인됐으며, 정규장·세션 전환 특성은 본장 스모크로 추가 기록한다.
- 최근월물 자동 선정과 만기 전환 방식을 별도 설계한다.
- 공식 지수선물 마스터 파일 자동 갱신 방식을 별도 설계한다.
## 백필 현재 상태

- 주식·ETF KRX 과거 1분봉 백필 기반을 추가했다.
- 실제 KIS `FHKST03010230` 호출에서 120행, 최신순, 행별 원문 보존을 확인했다.
- `job_id=2` 전체 백필은 996회 API 호출, 96,927행 조회·저장, 실패 0건으로 완료했다.
- 최종 DB 검증은 `scripts/backfill/verify_stock_minute_backfill.sh <job_id>`로 별도 실행한다.
- 선물 과거 백필은 계약·롤오버 목록 확정 전까지 보류한다.
