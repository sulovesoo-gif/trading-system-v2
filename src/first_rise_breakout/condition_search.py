"""KIS saved-condition discovery through the project's existing REST client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.collector.raw.kis_client import KISClientError


CONDITION_TITLE_PATH = "/uapi/domestic-stock/v1/quotations/psearch-title"
CONDITION_RESULT_PATH = "/uapi/domestic-stock/v1/quotations/psearch-result"
CONDITION_TITLE_TR_ID = "HHKST03900300"
CONDITION_RESULT_TR_ID = "HHKST03900400"


class SavedConditionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConditionCandidate:
    stock_code: str
    stock_name: str | None
    raw_payload: dict[str, Any]


class SavedConditionSearch:
    def __init__(self, client, *, user_id: str) -> None:
        if not user_id.strip():
            raise SavedConditionError("KIS_USER is required for saved-condition lookup")
        self.client = client
        self.user_id = user_id.strip()
        self.last_http_status: int | None = None
        self.last_kis_code: str | None = None

    @staticmethod
    def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        rows = payload.get("output2", [])
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise SavedConditionError("KIS saved-condition output2 is not an object list")
        return rows

    def resolve_seq(self, condition_name: str) -> str:
        payload = self.client.get(
            path=CONDITION_TITLE_PATH,
            tr_id=CONDITION_TITLE_TR_ID,
            params={"user_id": self.user_id},
        )
        matches = [row for row in self._rows(payload) if str(row.get("condition_nm", "")).strip() == condition_name]
        if len(matches) != 1:
            raise SavedConditionError(f"saved condition name must resolve exactly once: {condition_name}")
        seq = str(matches[0].get("seq", "")).strip()
        if not seq:
            raise SavedConditionError(f"saved condition has no seq: {condition_name}")
        return seq

    def candidates(self, seq: str) -> list[ConditionCandidate]:
        self.last_http_status = None
        self.last_kis_code = None
        try:
            payload = self.client.get(
                path=CONDITION_RESULT_PATH,
                tr_id=CONDITION_RESULT_TR_ID,
                params={"user_id": self.user_id, "seq": seq},
            )
        except KISClientError:
            # KIS documents MCA05918 for this API as the empty-result response.
            last_payload = getattr(self.client, "last_payload", None)
            self.last_http_status = getattr(self.client, "last_http_status", None)
            if isinstance(last_payload, dict):
                self.last_kis_code = str(last_payload.get("msg_cd") or last_payload.get("rt_cd") or "UNKNOWN")
            if isinstance(last_payload, dict) and last_payload.get("msg_cd") == "MCA05918":
                return []
            raise
        self.last_http_status = getattr(self.client, "last_http_status", None)
        self.last_kis_code = str(payload.get("msg_cd") or payload.get("rt_cd") or "0")
        result: list[ConditionCandidate] = []
        seen: set[str] = set()
        for row in self._rows(payload):
            code = str(row.get("code", "")).strip()
            if not code or code in seen:
                continue
            seen.add(code)
            result.append(ConditionCandidate(code, str(row.get("name") or "").strip() or None, row))
        return result
