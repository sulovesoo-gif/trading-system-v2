"""Durable, human-selected FORWARD_OBSERVATION candidate registry."""

from __future__ import annotations

from .contracts import ForwardCandidate, ForwardExecutionPath


class PostgresForwardRegistry:
    def __init__(self, connection_factory) -> None:
        self._connection_factory = connection_factory

    def register(self, candidate: ForwardCandidate) -> ForwardExecutionPath:
        if not candidate.selection_reason or not candidate.approved_by:
            raise ValueError("forward candidate requires explicit human selection audit")
        path = candidate.path
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO forward_execution_path
                   (forward_execution_id, entry_identity, exit_identity, execution_stock_code, active_yn)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (entry_identity, exit_identity, execution_stock_code) DO NOTHING""",
                (path.path_id, path.entry_identity, path.exit_identity, path.execution_stock_code,
                 "Y" if candidate.active else "N"),
            )
            cursor.execute(
                """INSERT INTO forward_candidate
                   (forward_candidate_id, forward_execution_id, strategy_reference, signal_stock_code,
                    selection_reason, approved_at, approved_by, active_yn)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (candidate.candidate_id, path.path_id, candidate.strategy_reference, candidate.signal_stock_code,
                 candidate.selection_reason, candidate.approved_at, candidate.approved_by,
                 "Y" if candidate.active else "N"),
            )
            connection.commit()
        return path
