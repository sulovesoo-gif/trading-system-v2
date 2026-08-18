"""Read-only source resolution for the independent Forward RAW collector."""

from __future__ import annotations


class ForwardSourceRegistry:
    """Resolve only the completed-minute instruments Forward can need.

    The frozen source stocks are always collected.  Explicitly activated Forward
    candidates may add their signal source and execution product without a code
    deployment.  This class deliberately performs no strategy evaluation.
    """

    FROZEN_SIGNAL_SOURCES = ("005930", "000660")

    def __init__(self, pool) -> None:
        self.pool = pool

    def stock_codes(self) -> tuple[str, ...]:
        sql = """
            SELECT c.signal_stock_code, p.execution_stock_code
              FROM forward_candidate c
              JOIN forward_execution_path p
                ON p.forward_execution_id = c.forward_execution_id
             WHERE c.active_yn = 'Y' AND p.active_yn = 'Y'
        """
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                rows = cursor.fetchall()
        codes = set(self.FROZEN_SIGNAL_SOURCES)
        for signal_stock_code, execution_stock_code in rows:
            codes.add(str(signal_stock_code))
            codes.add(str(execution_stock_code))
        return tuple(sorted(codes))
