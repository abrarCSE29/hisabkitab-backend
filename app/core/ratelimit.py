"""Per-user sliding-window rate limiting.

In-memory and per-process — sufficient for the single-instance free-tier
deployment this app targets. If the backend ever scales horizontally, swap
the storage for a shared store (e.g. Redis) behind the same interface.
"""

import threading
import time
from collections import deque

from fastapi import Depends, HTTPException, status

from app.core.security import AuthenticatedUser, get_current_user

_REGISTRY: list["SlidingWindowLimiter"] = []


class SlidingWindowLimiter:
    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        _REGISTRY.append(self)

    def hit(self, key: str) -> float | None:
        """Record an attempt; return seconds-until-allowed when over the limit.

        Attempts count whether or not the request later succeeds, so failed
        guesses (e.g. invalid join codes) consume the budget too.
        """
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > self.window_seconds:
                hits.popleft()
            if len(hits) >= self.max_requests:
                return self.window_seconds - (now - hits[0])
            hits.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def reset_all_limiters() -> None:
    """Test hook: clear accumulated hits between test cases."""
    for limiter in _REGISTRY:
        limiter.reset()


def user_rate_limit(limiter: SlidingWindowLimiter, action: str):
    """Route dependency limiting each authenticated user separately."""

    def dependency(user: AuthenticatedUser = Depends(get_current_user)) -> None:
        retry_after = limiter.hit(f"{action}:{user.id}")
        if retry_after is not None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many {action} requests — please try again later",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

    return dependency
