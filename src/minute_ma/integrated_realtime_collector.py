"""Isolated H0UNCNT0 collector for Minute MA INTEGRATED signal sources."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from datetime import datetime, timedelta
from typing import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4
from zoneinfo import ZoneInfo

from .integrated_realtime_contracts import (
    INTEGRATED_SIGNAL_CODES,
    TR_INTEGRATED_EXECUTION,
    IntegratedRealtimeContractError,
    integrated_source_datetime,
    split_integrated_execution_frame,
)

KST = ZoneInfo("Asia/Seoul")
LOGGER = logging.getLogger(__name__)


class MinuteMaIntegratedCollectorError(RuntimeError):
    pass


def issue_approval_key(*, base_url: str, app_key: str, app_secret: str) -> str:
    body = json.dumps({
        "grant_type": "client_credentials", "appkey": app_key, "secretkey": app_secret,
    }).encode()
    request = Request(
        f"{base_url.rstrip('/')}/oauth2/Approval", data=body,
        headers={"content-type": "application/json; charset=utf-8"}, method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode())
    except (HTTPError, OSError, ValueError) as error:
        raise MinuteMaIntegratedCollectorError(
            f"KIS integrated websocket approval failed: {type(error).__name__}"
        ) from error
    key = payload.get("approval_key") if isinstance(payload, dict) else None
    if not isinstance(key, str) or not key:
        raise MinuteMaIntegratedCollectorError("KIS approval response has no approval_key")
    return key


class MinuteMaIntegratedExecutionCollector:
    SYMBOLS = INTEGRATED_SIGNAL_CODES

    def __init__(self, repository, *, ws_url: str, approval_provider: Callable[[], str],
                 now_provider: Callable[[], datetime] | None = None) -> None:
        self.repository = repository
        self.ws_url = ws_url
        self.approval_provider = approval_provider
        self.now = now_provider or (lambda: datetime.now(KST).replace(tzinfo=None))
        self.instance_id = uuid4()
        self._sequence = 0
        self._reconnect_count = 0
        self._last_event_time: dict[str, datetime] = {}
        self._hash_queue: deque[tuple[str, str, str]] = deque(maxlen=200_000)
        self._hashes: set[tuple[str, str, str]] = set()

    @property
    def subscriptions(self) -> list[dict[str, str]]:
        return [{"tr_id": TR_INTEGRATED_EXECUTION, "tr_key": code} for code in self.SYMBOLS]

    def _remember_hash(self, identity: tuple[str, str, str]) -> bool:
        duplicate = identity in self._hashes
        if not duplicate:
            if len(self._hash_queue) == self._hash_queue.maxlen:
                self._hashes.discard(self._hash_queue[0])
            self._hash_queue.append(identity)
            self._hashes.add(identity)
        return duplicate

    async def run_forever(self) -> None:
        import websockets

        for identity in self.repository.recent_hashes(since=self.now() - timedelta(minutes=10)):
            self._remember_hash(identity)
        backoff = 1
        while True:
            connection_id = uuid4()
            connected_at = self.now()
            reconnect = self._reconnect_count > 0
            try:
                approval = await asyncio.to_thread(self.approval_provider)
                async with websockets.connect(self.ws_url, ping_interval=None, close_timeout=5) as socket:
                    self._sequence = 0
                    self.repository.open_connection(
                        connection_id=connection_id, collector_instance_id=self.instance_id,
                        connected_at=connected_at, reconnect_flag=reconnect,
                        subscriptions=self.subscriptions,
                    )
                    for subscription in self.subscriptions:
                        await socket.send(json.dumps({
                            "header": {"approval_key": approval, "custtype": "P", "tr_type": "1",
                                       "content-type": "utf-8"},
                            "body": {"input": subscription},
                        }))
                        acknowledgement = await asyncio.wait_for(socket.recv(), timeout=3.0)
                        if isinstance(acknowledgement, bytes):
                            acknowledgement = acknowledgement.decode("utf-8")
                        if not acknowledgement.startswith("{"):
                            raise MinuteMaIntegratedCollectorError(
                                f"KIS subscription acknowledgement is not JSON for {subscription['tr_key']}"
                            )
                        safe_ack = self._safe_ack(json.loads(acknowledgement))
                        LOGGER.info("Minute MA INTEGRATED subscription response=%s", safe_ack)
                        if str(safe_ack.get("rt_cd")) != "0":
                            raise MinuteMaIntegratedCollectorError(
                                f"KIS H0UNCNT0 subscription rejected code={subscription['tr_key']} "
                                f"msg={safe_ack.get('msg1')}"
                            )
                        await asyncio.sleep(0.6)
                    LOGGER.info(
                        "Minute MA INTEGRATED websocket connected connection_id=%s subscriptions=%d",
                        connection_id, len(self.subscriptions),
                    )
                    backoff = 1
                    first_data = True
                    while True:
                        try:
                            frame = await asyncio.wait_for(socket.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        received_at = self.now()
                        if isinstance(frame, bytes):
                            frame = frame.decode("utf-8")
                        if frame.startswith("{"):
                            message = json.loads(frame)
                            if message.get("header", {}).get("tr_id") == "PINGPONG":
                                await socket.send(frame)
                            continue
                        self._sequence += 1
                        try:
                            events = split_integrated_execution_frame(frame)
                        except IntegratedRealtimeContractError as error:
                            LOGGER.error("H0UNCNT0 invalid frame sequence=%d error=%s", self._sequence, error)
                            continue
                        for event in events:
                            symbol = event.values.get("MKSC_SHRN_ISCD", "")
                            if symbol not in self.SYMBOLS:
                                continue
                            if not event.values.get("STCK_PRPR") or not event.values.get("ACML_VOL"):
                                LOGGER.error("H0UNCNT0 core fields missing sequence=%d index=%d",
                                             self._sequence, event.event_index)
                                continue
                            event_time = integrated_source_datetime(event, received_at=received_at)
                            previous = self._last_event_time.get(symbol)
                            regression = previous is not None and event_time < previous
                            self._last_event_time[symbol] = max(previous, event_time) if previous else event_time
                            identity = (TR_INTEGRATED_EXECUTION, symbol, event.payload_hash)
                            duplicate = self._remember_hash(identity)
                            self.repository.save_event(
                                event, received_at=received_at, connection_id=connection_id,
                                collector_instance_id=self.instance_id, receive_sequence=self._sequence,
                                reconnect_flag=reconnect and first_data, source_gap_flag=False,
                                event_time_regression_flag=regression, duplicate_flag=duplicate,
                            )
                            first_data = False
            except asyncio.CancelledError:
                self.repository.close_connection(
                    connection_id, disconnected_at=self.now(), status="DISCONNECTED",
                    reason="graceful shutdown", last_sequence=self._sequence,
                )
                raise
            except Exception as error:
                try:
                    self.repository.close_connection(
                        connection_id, disconnected_at=self.now(), status="FAILED",
                        reason=f"{type(error).__name__}: {error}", last_sequence=self._sequence,
                    )
                except Exception:
                    LOGGER.exception("Minute MA INTEGRATED connection close audit failed")
                self._reconnect_count += 1
                LOGGER.warning("Minute MA INTEGRATED reconnect=%d backoff=%ds reason=%s",
                               self._reconnect_count, backoff, type(error).__name__)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    @staticmethod
    def _safe_ack(message: dict) -> dict:
        body = message.get("body", {}) if isinstance(message, dict) else {}
        return {"tr_id": message.get("header", {}).get("tr_id"),
                "msg1": body.get("msg1"), "rt_cd": body.get("rt_cd")}


def collector_from_environment(repository) -> MinuteMaIntegratedExecutionCollector:
    base_url = os.getenv("KIS_BASE_URL", "")
    app_key = os.getenv("KIS_API_KEY", "")
    app_secret = os.getenv("KIS_API_SECRET", "")
    missing = [name for name, value in (
        ("KIS_BASE_URL", base_url), ("KIS_API_KEY", app_key), ("KIS_API_SECRET", app_secret),
    ) if not value]
    if missing:
        raise MinuteMaIntegratedCollectorError(
            f"missing Minute MA INTEGRATED configuration: {','.join(missing)}")
    return MinuteMaIntegratedExecutionCollector(
        repository, ws_url=os.getenv("KIS_WS_URL", "ws://ops.koreainvestment.com:21000"),
        approval_provider=lambda: issue_approval_key(
            base_url=base_url, app_key=app_key, app_secret=app_secret),
    )
