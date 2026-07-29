"""ntfy 연결만 검증한다. 신호·알림 이력과 DB에는 기록하지 않는다."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.service.ntfy_alert_service import NtfyAlertService, NtfySettings


def main() -> int:
    load_dotenv(ROOT / ".env")
    NtfyAlertService(NtfySettings.from_environment()).send(
        subject="Trading System V2 테스트",
        body="SK하이닉스 SMA5/SMA10 크로스 알림 연결 테스트입니다.\n실제 주문은 실행하지 않습니다.",
    )
    print("ntfy 테스트 알림 전송 성공")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
