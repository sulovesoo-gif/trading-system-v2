# Minute MA INTEGRATED L0 RAW 보존 / PURGE

## 범위와 조사 근거

Leadership `7da9af57c750bb50788a861ac977ff1849466fa8` 이후 독립 변경.
기존 Runtime/collector/builder/evaluator/FLOW/Leadership 소스 및 unit은 변경하지 않는다.

- `src/flow_raw/collector.py`: INTEGRATED recent_hashes 조회 최근 10분.
- `src/minute_ma/integrated_realtime_repository.py`: run_recent 최근 3분;
  run_startup_backlog 당일 마지막 bar - 1분 또는 당일 00:00부터. 자정 부근의
  이전 분 접근도 D-1 안전버퍼 안이다. 더 오래된 RAW를 요구하는 경로는 확인되지 않았다.
- `scripts/runtime/run_minute_ma_integrated_1min_builder.py`: 완성봉은 immutable insert;
  grace 기본 2000ms. 늦은 RAW를 기존 bar가 반영하지 못하면 PURGE를 차단한다.
- 기존 EOD/maintenance에 이 RAW 보존을 담당하는 안전한 hook은 없었다.
  기존 독립 oneshot/timer 패턴을 사용하되 Trading unit 의존성은 넣지 않는다.
- 2026-09-15 운영 READ ONLY 확인: TimescaleDB 2.28.3;
  RAW hypertable의 유일한 시간차원은 `received_at timestamp without time zone`, 간격 7일.
  `_timescaledb_internal._hyper_72_490_chunk`의 실제 CHECK는
  `2026-09-10 00:00 <= received_at < 2026-09-17 00:00`.
  catalog timestamptz의 KST 표시는 09:00이지만 naive RAW 경계는 00:00이다.
  그래서 catalog 경계는 `AT TIME ZONE 'UTC'`로 복원한다. 추정 9시간 이동을 하지 않는다.
- 운영 RAW 범위 추가 조회는 15초 statement timeout에 걸렸다. 반복/전체 스캔하지 않았다.
  운영에는 어떠한 DDL/DML/파일 배포/재시작/주문도 수행하지 않았다.

## 보존 계약

대상은 오직 `public.raw_minute_ma_integrated_execution`이다.
두 종목 각각 RAW source_event_time의 실제 최근 2개 날짜를 읽는다. 각 종목의 두 번째
날짜 중 가장 오래된 날짜 00:00을 cutoff로 한다. 현재 날짜 전체도 항상 보존한다.
월요일에 데이터가 있으면 금요일이 D-1이다. 주말/휴장/한 종목 지연 시에는
최근 2개 실제 데이터 날짜를 보존하므로 계약보다 더 보수적으로 보존할 수 있다.
어느 한 종목이라도 2개 날짜를 확인할 수 없으면 SKIP한다.

`range_end <= cutoff`인 완전한 chunk만 후보다. 경계에 걸친 chunk는 보존한다.
기존 7일 chunk는 재작성/분할하지 않고, 전체 범위가 cutoff 이전으로 넘어간 뒤에만
동일 검증을 거쳐 후보가 될 수 있다. 현재 남은 9/10~9/17 chunk는 지금 제거되지 않는다.

신규 migration은 `set_chunk_time_interval(..., INTERVAL '1 day')`만 수행한다.
기존 chunk에는 영향을 주지 않는 공식 방식이다.
[Timescale 공식 설명](https://www.tigerdata.com/blog/13-tips-to-improve-postgresql-insert-performance),
[drop_chunks 공식 API](https://docs.timescale.com/api/latest/hypertable/drop_chunks/).
날짜만으로 삭제하는 Timescale retention policy는 등록하지 않는다.

## 완료봉 선행검증과 fail-closed

1. 기본 실행은 REPEATABLE READ / READ ONLY dry-run이며 쓰기 lock/drop이 없다.
2. apply는 transaction advisory lock으로 maintenance 중복실행을 차단한다.
   삭제 후보 cold chunk만 SHARE lock으로 보호한다. parent/current chunk를 직접 잠그지 않는다.
3. 후보 RAW의 source/business/received 날짜 불일치, D-0/D-1 유입, 계약 외 종목/venue/TR을 차단한다.
4. 후보가 포함한 source 날짜의 전체 tick을 서버 커서로 읽고 최대 3개 분 그룹만 유지한다.
   기존 순수 `build_realtime_minute_bars` 함수로 검산하며 RAW/봉을 쓰지 않는다.
5. OHLC, 누적/체결/분 거래량, event/message 수, source/receive 범위,
   connection/duplicate 수, 품질 flag를 저장된 봉과 비교한다.
6. 봉 누락, 늦은 tick 불일치, source gap, 순서/누적량/시각 역전, 알 수 없는 품질은 SKIP.
   **품질을 정상으로 보정하지 않는다.** 첫 실제 분의 이전 누적량 부재는 기존 builder가
   `INCOMPLETE/PREVIOUS_MINUTE_ACCUMULATED_VOLUME_MISSING`으로 기록한다.
   이 경계만 검산 결과가 정확히 같고 이미 finalization된 경우 허용한다.
   마지막 grace 봉과 reconnect 품질도 기존 표시 그대로 완전 일치해야 한다.
   중복 tick만 있는 분은 builder 계약대로 봉을 합성하지 않는다.
7. 모든 후보 검증이 끝난 후 각 정확한 `[range_start, range_end)`에만 drop_chunks를 호출한다.
   반환 chunk가 기대값과 다르면 transaction 전체 rollback.
8. SQL/lock timeout, 권한/DB 오류, 프로세스 종료 등은 rollback. 재시도용 자동 주문이나
   다른 서비스의 정지/재기동은 없다. 원인 해소 전 대량 DELETE 등 우회를 하지 않는다.

품질 문제로 장기간 SKIP하면 디스크가 다시 찰 수 있다. retention은 미완성 봉을
자동 복구하지 않으며 디스크 안전을 보장하는 대체 장치도 아니다. 운영자는 로그/여유공간을 확인한다.
512MB MemoryMax, 45분 실행 한도, statement timeout 기본 120초, lock timeout 1초를 사용한다.
delete 전후 chunk 총크기는 예상 회수량이며 실제 filesystem 변화는 동시 수집/WAL 영향이 있다.

## 일배치

기존 수집기의 MARKET_LIVENESS_END=20:00 이후 **매일 21:30 KST + 최대 120초 지연**.
완료봉 생성 여부 자체는 위 검증이 최종 기준이다. 날짜/시각만으로 완료라고 간주하지 않는다.
Persistent=false로 부팅 직후 장중 catch-up을 방지한다. CLI도 21:30~24:00 KST 밖은 SKIP.
DB-only 전용 env 파일을 쓰며 KIS key/계좌정보가 필요하지 않다.
기존 builder grace를 바꿨다면 service의 --grace-ms도 동일하게 맞춰야 한다.

## 로컬 검증

```powershell
python -m unittest test.test_minute_ma_integrated_raw_retention test.test_minute_ma_integrated_realtime -v
# 별도 local PostgreSQL 데이터베이스만 허용. 운영 DSN은 거부한다.
$env:RETENTION_TEST_DSN='host=127.0.0.1 port=55491 dbname=minute_ma_retention_test user=postgres'
python -m unittest test.test_minute_ma_integrated_raw_retention_postgres -v
```

2026-09-15 로컬 결과: 신규 단위 29 + 로컬 PostgreSQL 9 + 기존 INTEGRATED 5 +
기존 FLOW collector 15 + completed-minute collector 5 = 63 PASS / 0 FAIL / 0 SKIP.
CLI --help 정상. 기존 파일 변경 0. Ubuntu systemd 실제 실행 검증은 운영 미실행 조건상 미완료다.

PostgreSQL 통합시험은 실제 SQL/stream/lock/rollback을 검증하지만 Timescale metadata와
drop_chunks는 명시적인 test double이다. 로컬 Docker daemon 부재로 실제 Timescale 2.28.3
확장함수의 migration/drop_chunks 통합시험은 미완료. 배포 승인 전 별도 Timescale 시험환경에서
이를 확인해야 한다. 운영 DB에서는 삭제 시험을 하지 않는다.

## 운영 적용 절차 — 이번 작업에서는 실행하지 않음

다음은 별도 배포 승인 및 실제 Timescale 시험 PASS 이후 Ubuntu에서 실행할 명령이다.
코드가 운영 repository에 반영되어 있다는 전제다. 기존 Trading 서비스 재시작은 필요 없다.

### 0. DB-only 환경 준비 (최초 1회)

기존 `.env`에서 DB 키만 추출한다. 이미 파일이 있으면 덮어쓰지 않고 내용을 운영자가 확인한다.
DB 사용자는 SELECT 및 대상 hypertable의 chunk 관리 권한이 필요하다.

```bash
set -euo pipefail
cd /home/ubuntu/projects/trading-system-v2
test ! -e /etc/trading-minute-ma-integrated-retention.env && \
  sudo install -o root -g ubuntu -m 0640 /dev/null /etc/trading-minute-ma-integrated-retention.env && \
  grep -E '^DB_(HOST|PORT|NAME|USER|PASSWORD)=' .env | \
  sudo tee /etc/trading-minute-ma-integrated-retention.env >/dev/null
```

### 14. Migration (미래 chunk 1일 간격)

```bash
venv/bin/python scripts/maintenance/purge_minute_ma_integrated_raw.py \
  --env-file /etc/trading-minute-ma-integrated-retention.env --apply \
  --sql-file database/migrations/20260915_minute_ma_integrated_raw_chunk_interval.sql
```

### 15. systemd 파일 설치 (아직 기동/enable하지 않음)

```bash
sudo install -m 0644 systemd/trading-minute-ma-integrated-raw-retention.service /etc/systemd/system/
sudo install -m 0644 systemd/trading-minute-ma-integrated-raw-retention.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/trading-minute-ma-integrated-raw-retention.service /etc/systemd/system/trading-minute-ma-integrated-raw-retention.timer
```

### 16. 최초 수동 dry-run (21:30 이후, --apply 없음)

```bash
venv/bin/python scripts/maintenance/purge_minute_ma_integrated_raw.py \
  --env-file /etc/trading-minute-ma-integrated-retention.env
```

DRY_RUN_PASS 또는 NO_COMPLETE_OLD_CHUNKS를 확인한다. 그 외 SKIP/FAIL은
원인을 해결하기 전 timer를 활성화하지 않는다. 대량 삭제/미완성 품질 무시로 우회하지 않는다.

### 17. 자동 timer 활성화 (별도 승인 후)

```bash
sudo systemctl enable --now trading-minute-ma-integrated-raw-retention.timer
systemctl list-timers trading-minute-ma-integrated-raw-retention.timer --all
```

### 18. 적용 후 READ ONLY 확인

```bash
venv/bin/python scripts/maintenance/purge_minute_ma_integrated_raw.py \
  --env-file /etc/trading-minute-ma-integrated-retention.env \
  --sql-file scripts/ops/verify_minute_ma_integrated_raw_retention_readonly.sql
journalctl -u trading-minute-ma-integrated-raw-retention.service -n 100 --no-pager
df -h /
df -i /
```

확인: dimension=1 day, 기존 chunk 경계 보존, 대상 RAW chunk만 감소,
완료봉 보존, D0/D1 cutoff 및 제거 목록, SKIP/FAIL 여부. 반환 코드 0=완료/후보없음,
2=안전조건 SKIP, 1=DB/실행 FAIL. 날짜마다 성공 로그 없이 조용히 지나간 것으로 판단하지 않는다.
