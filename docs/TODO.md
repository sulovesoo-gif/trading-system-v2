# 현재 작업

- [ ] Ubuntu 테스트 서버에서 `scripts/db/start_test_db.sh`로 TimescaleDB 컨테이너를 기동한다.
- [ ] `DB_INTEGRATION_TEST=1` 환경에서 `scripts/db/run_integration_test.sh`를 실행한다.
- [ ] 최근월물 자동 선정 방식을 설계한다.
- [ ] 만기 도래 시 다음 월물 전환 기준을 설계한다.
- [ ] 한국투자증권 공식 지수선물 마스터 파일 자동 갱신 방식을 설계한다.

## 저장 계층 후속 작업

- [ ] 운영 DB 연결 정보와 권한 정책을 확정한다.
- [ ] RAW 정정 데이터 처리 정책을 검토한다. 현재는 `ON CONFLICT DO NOTHING`으로 기존 행을 갱신하지 않는다.
- [ ] RAW 보관 기간 및 TimescaleDB 압축 정책을 별도 설계한다.