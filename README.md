# Trading System V2

## 실행 환경

- 공식 지원 Python 버전: `>=3.10, <3.15`
- 운영 서버 기준: Ubuntu ARM64, Python 3.10 지원
- 모든 시간은 한국시간(KST, `Asia/Seoul`)을 기준으로 처리한다.

## 의존성 파일

- `requirements.txt`: RAW Collector·Repository 런타임 의존성
- `requirements-dev.txt`: 개발·단위 테스트 환경
- `requirements-analysis.txt`: 향후 분석 계층 전용 의존성. 현재는 비어 있다.

RAW 수집·저장 계층은 NumPy, pandas, SciPy를 사용하지 않는다. 분석 계층을 구현할 때 실제 사용처가 확정된 패키지만 별도로 추가한다.
