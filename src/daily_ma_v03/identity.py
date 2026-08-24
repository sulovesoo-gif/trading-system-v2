"""Deterministic hashes used by PAPER-only event and transition persistence."""

from __future__ import annotations

import hashlib
import json
from typing import Mapping


def snapshot_hash(snapshot: Mapping[str, object]) -> str:
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def transition_key(*, paper_trade_id: int, transition_type: str,
                   source_bar_time: object | None) -> str:
    payload = f"DAILY_MA_V03|{paper_trade_id}|{transition_type}|{source_bar_time}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
