"""Small in-process sliding-window rate limiter.

Good enough for a single-process prototype.  For several workers replace the store with Redis
(the interface is intentionally tiny)."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request, status

from app.config import settings


class RateLimiter:
    def __init__(self) -> None:
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def check(self, key: str, limit: int, window_sec: int) -> None:
        if not settings.RATE_LIMIT_ENABLED:
            return
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_sec:
                q.popleft()
            if len(q) >= limit:
                retry = max(1, int(window_sec - (now - q[0])))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests. Please wait and try again.",
                    headers={"Retry-After": str(retry)},
                )
            q.append(now)


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limit(scope: str, limit: int, window_sec: int = 60, per_user: bool = False):
    """FastAPI dependency factory.  Keyed by IP (and by user id when `per_user`)."""
    def _dep(request: Request):
        ident = client_ip(request)
        if per_user:
            auth = request.headers.get("authorization", "")
            ident += "|" + auth[-24:]
        limiter.check(f"{scope}:{ident}", limit, window_sec)
    return _dep
