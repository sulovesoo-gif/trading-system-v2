"""KIS access token local cache. Secrets other than the token are never stored."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")


class TokenCacheError(RuntimeError):
    """Local token cache cannot be safely read or written."""


class TokenCache:
    """Stores only an access token and its KST expiration timestamp."""

    LOCK_WAIT_SECONDS = 5.0
    LOCK_POLL_SECONDS = 0.05

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")

    def load(self) -> tuple[str, datetime] | None:
        try:
            content = self.path.read_text(encoding="utf-8")
            payload = json.loads(content)
            token = payload["access_token"]
            expires_at = datetime.fromisoformat(payload["expires_at"])
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            self.discard()
            return None
        if not isinstance(token, str) or not token or expires_at.tzinfo is None:
            self.discard()
            return None
        return token, expires_at.astimezone(KST)

    def save(self, *, access_token: str, expires_at: datetime) -> None:
        if not access_token:
            raise TokenCacheError("Empty access token cannot be cached.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        payload = json.dumps(
            {"access_token": access_token, "expires_at": expires_at.astimezone(KST).isoformat()},
            ensure_ascii=False,
        )
        try:
            with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self._restrict_permissions(temporary)
            os.replace(temporary, self.path)
            self._restrict_permissions(self.path)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise TokenCacheError("KIS token cache could not be written.") from error

    def discard(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    @contextmanager
    def refresh_lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.LOCK_WAIT_SECONDS
        acquired = False
        while time.monotonic() < deadline:
            try:
                descriptor = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(descriptor)
                acquired = True
                break
            except FileExistsError:
                time.sleep(self.LOCK_POLL_SECONDS)
        if not acquired:
            raise TokenCacheError("KIS token cache refresh lock could not be acquired.")
        try:
            self._restrict_permissions(self.lock_path)
            yield
        finally:
            try:
                self.lock_path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _restrict_permissions(path: Path) -> None:
        """Best-effort current-user access restriction; Windows ACLs remain host managed."""
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
