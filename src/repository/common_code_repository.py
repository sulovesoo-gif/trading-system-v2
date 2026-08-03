"""공통코드를 안전한 설정 객체로 읽는 Repository."""

from __future__ import annotations

from dataclasses import dataclass


class CommonCodeError(RuntimeError):
    pass


@dataclass(frozen=True)
class StockConfig:
    stock_code: str
    stock_name: str
    instrument_type: str
    minute_collect_yn: bool
    analysis_yn: bool
    alert_yn: bool
    default_market_code: str
    program_collect_yn: bool

@dataclass(frozen=True)
class ApiScheduleConfig:
    code: str
    interval_unit: str
    interval_value: int
    execution_second: int
    start_time: str
    end_time: str
    enabled: bool
    def due(self, now) -> bool:
        return self.enabled and self.interval_unit == "MIN" and now.second == self.execution_second and self.start_time <= now.strftime("%H:%M") <= self.end_time


@dataclass(frozen=True)
class MaConfig:
    code: str
    short_period: int
    mid_period: int
    long_period: int
    price_field: str
    ma_type: str
    include_in_progress: bool

    def __post_init__(self) -> None:
        if not (0 < self.short_period < self.mid_period < self.long_period):
            raise CommonCodeError("이동평균 기간은 단기 < 중기 < 장기여야 합니다.")
        if self.ma_type != "SMA":
            raise CommonCodeError("현재 구현은 SMA만 지원합니다.")
        if self.price_field not in PRICE_FIELDS:
            raise CommonCodeError("허용되지 않은 가격 기준 코드입니다.")


PRICE_FIELDS = frozenset({"OPEN", "HIGH", "LOW", "CLOSE", "HL2", "HLC3", "OHLC4", "CURRENT_PRICE"})


class CommonCodeRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    def enabled_minute_stocks(self) -> list[StockConfig]:
        sql = (
            "SELECT code, code_name, attr1, attr2, attr4, attr5, attr7, attr10 "
            "FROM common_code WHERE group_cd = 'STOCK' AND use_yn = 'Y' AND attr2 = 'Y' ORDER BY CAST(attr9 AS INTEGER), code"
        )
        return [
            StockConfig(row[0], row[1], row[2], row[3] == "Y", row[4] == "Y", row[5] == "Y", row[6], row[7] == "Y")
            for row in self._fetchall(sql)
        ]

    def active_ma_config(self, code: str) -> MaConfig:
        row = self._fetchone(
            "SELECT code, attr1, attr2, attr3, attr4, attr5, attr6 FROM common_code "
            "WHERE group_cd = 'MA_CONFIG' AND code = %s AND use_yn = 'Y'",
            (code,),
        )
        if row is None:
            raise CommonCodeError(f"활성 이동평균 설정이 없습니다: {code}")
        return MaConfig(row[0], int(row[1]), int(row[2]), int(row[3]), row[4], row[5], row[6] == "Y")

    def switch_enabled(self, code: str) -> bool:
        row = self._fetchone("SELECT attr1 FROM common_code WHERE group_cd = 'SYSTEM_SWITCH' AND code = %s AND use_yn = 'Y'", (code,))
        return row is not None and row[0] == "Y"

    def strategy_configs(self) -> list[dict[str, str | None]]:
        rows = self._fetchall(
            "SELECT code, attr1, attr2, attr3, attr4, attr5, attr6, attr7, attr8 "
            "FROM common_code WHERE group_cd = 'STRATEGY' AND use_yn = 'Y' ORDER BY CAST(attr9 AS INTEGER), code"
        )
        return [dict(zip(("strategy_code", "strategy_yn", "analysis_yn", "alert_yn", "trade_yn", "stock_code", "market_code", "ma_config_code", "price_field_code"), row)) for row in rows]

    def api_schedule(self, code: str) -> ApiScheduleConfig:
        row = self._fetchone("SELECT code, attr1, attr2, attr5, attr6, attr7, attr8 FROM common_code WHERE group_cd = 'API_SCHEDULE' AND code = %s AND use_yn = 'Y'", (code,))
        if row is None:
            return ApiScheduleConfig(code, "MIN", 1, -1, "99:99", "00:00", False)
        return ApiScheduleConfig(row[0], row[1], int(row[2]), int(row[3]), row[4], row[5], row[6] == "Y")

    def _fetchone(self, sql, params=()):
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchone()

    def _fetchall(self, sql, params=()):
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()
