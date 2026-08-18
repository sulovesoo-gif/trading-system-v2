"""Durable, human-selected FORWARD_OBSERVATION candidate registry."""

from __future__ import annotations

from datetime import datetime

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

    def active_candidates(self) -> tuple[ForwardCandidate, ...]:
        """Reload human-approved active candidates without a code deployment."""
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT c.forward_candidate_id,c.strategy_reference,c.signal_stock_code,
                          c.selection_reason,c.approved_at,c.approved_by,
                          p.entry_identity,p.exit_identity,p.execution_stock_code
                     FROM forward_candidate c JOIN forward_execution_path p
                       ON p.forward_execution_id=c.forward_execution_id
                    WHERE c.active_yn='Y' AND p.active_yn='Y'
                    ORDER BY c.created_at,c.forward_candidate_id"""
            )
            rows = cursor.fetchall()
        return tuple(
            ForwardCandidate(
                candidate_id=str(row[0]), strategy_reference=str(row[1]),
                signal_stock_code=str(row[2]), selection_reason=str(row[3]),
                approved_at=row[4] if isinstance(row[4], datetime) else row[4],
                approved_by=str(row[5]),
                path=ForwardExecutionPath(str(row[6]), str(row[7]), str(row[8])), active=True,
            ) for row in rows
        )

    def deactivate_research_path(self, path: ForwardExecutionPath) -> int:
        """Exclude a Research path without deleting its registry/audit history."""
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE forward_candidate c
                      SET active_yn='N'
                     WHERE c.forward_execution_id=%s
                       AND c.strategy_reference LIKE 'RESEARCH_STRATEGY_%%'
                       AND c.active_yn='Y'""",
                (path.path_id,),
            )
            changed = cursor.rowcount
            connection.commit()
        return changed
