# 원격 테스트 서버 운영 절차

## 환경

- Windows Codex에서는 OpenSSH `trading-v2` 별칭으로 접속한다.
- 원격 프로젝트 경로는 `/home/ubuntu/projects/trading-system-v2`이다.
- 서버는 Ubuntu ARM64, Python 3.10 환경이다.
- 테스트 DB는 `trading_system_v2_test`만 사용한다.
- TimescaleDB는 Docker 컨테이너에서 실행되며, PostgreSQL 16 / TimescaleDB 2.28.3을 사용한다.

SSH 별칭과 인증 설정은 사용자 홈의 `~/.ssh/config`에서 관리한다. 개인키 경로, 서버 IP, 비밀번호, API 키는 이 문서에 기록하지 않는다.

## 기본 작업 흐름

```text
Windows 로컬 수정
→ 로컬 테스트
→ commit
→ origin/main push
→ 원격 pull --ff-only
→ 원격 통합 테스트
```

원격에서 작업하기 전에는 로컬·원격 작업 트리와 HEAD를 비교해 동기화 상태를 확인한다.

## 원격 작업 전 확인

원격 프로젝트 경로에서 다음 상태만 먼저 확인한다.

- `hostname`
- `whoami`
- `pwd`
- `git status --short`
- `git rev-parse HEAD`

프로젝트 경로 밖의 파일은 수정하지 않는다.

## 안전 원칙

- `trading_system_v2_test` 테스트 DB만 사용한다.
- 운영 DB에는 접근하거나 변경하지 않는다.
- 비밀번호, 개인키, API 키 등 인증정보를 출력하거나 문서화하지 않는다.
- 삭제, 초기화, DB 파괴, Docker 볼륨 삭제 등 파괴적 명령은 사용자 승인 없이 실행하지 않는다.
- 원격 변경 전에는 변경 범위와 영향을 확인한다.
