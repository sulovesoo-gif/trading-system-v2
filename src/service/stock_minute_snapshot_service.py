"""진행 중 1분봉 관찰 행을 완료봉 RAW와 분리해 생성한다."""

from __future__ import annotations

from datetime import datetime, timedelta


SCHEDULED_SNAPSHOT_SECONDS = (0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55)


class StockMinuteSnapshotService:
    @staticmethod
    def build_snapshot(*, collector_rows: list[dict[str, object]], observed_at: datetime) -> dict[str, object] | None:
        if observed_at.second not in SCHEDULED_SNAPSHOT_SECONDS:
            raise ValueError("진행봉 스냅샷은 승인된 5초 기준에서만 생성합니다.")
        minute = observed_at.replace(second=0, microsecond=0)
        # 정각 응답은 롤오버 중일 수 있으므로 관찰 대상은 직전 분으로 명시한다.
        target = minute - timedelta(minutes=1) if observed_at.second == 0 else minute
        matches = [row for row in collector_rows if row.get("bar_time") == target]
        if len(matches) != 1:
            return None
        source = matches[0]
        return {
            # snapshot_time은 Timescale hypertable의 파티션·중복 키이므로 예정된 5초 슬롯으로 고정한다.
            # 실제 응답 수신 시각은 Collector의 collected_at을 보존한다.
            "snapshot_time": observed_at.replace(microsecond=0),
            "target_bar_time": target,
            "collected_at": source["collected_at"],
            "data_source": source["data_source"],
            "market_code": source["market_code"],
            "trading_venue": source["trading_venue"],
            "collect_cycle": "5SEC",
            "stock_code": source["stock_code"],
            "snapshot_second": observed_at.second,
            "open_price": source["open_price"],
            "high_price": source["high_price"],
            "low_price": source["low_price"],
            "close_price": source["close_price"],
            "volume": source["volume"],
            "accumulated_amount": source["accumulated_amount"],
            "raw_payload": source["raw_payload"],
        }
