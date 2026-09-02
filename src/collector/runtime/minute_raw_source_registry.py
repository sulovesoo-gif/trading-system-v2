"""Common-code registry for completed one-minute RAW collection targets."""

from __future__ import annotations


class MinuteRawSourceRegistry:
    """Resolve enabled STOCK minute targets independently of strategy registries."""

    def __init__(self, pool) -> None:
        self.pool = pool

    def stock_codes(self) -> tuple[str, ...]:
        sql = """
            SELECT c.code
              FROM common_code c
              JOIN common_code_group g
                ON g.group_cd = c.group_cd
             WHERE c.group_cd = 'STOCK'
               AND g.use_yn = 'Y'
               AND c.use_yn = 'Y'
               AND c.attr2 = 'Y'
             ORDER BY c.sort_order, c.code
        """
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                rows = cursor.fetchall()

        stock_codes: list[str] = []
        for (raw_stock_code,) in rows:
            stock_code = str(raw_stock_code or "").strip()
            if not stock_code:
                raise ValueError("enabled minute RAW source has an empty STOCK code")
            stock_codes.append(stock_code)
        return tuple(stock_codes)
