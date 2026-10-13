"""Bound repeated dashboard login failures in this process.

The limit is per username and also global. It does not distinguish a missing
user from a wrong password. Another worker process has its own counters.
"""

import time


class LoginLimiter:
    def __init__(
        self,
        *,
        limit: int = 8,
        window_seconds: float = 600,
        global_limit: int = 200,
        clock=time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._global_limit = global_limit
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._global: list[float] = []

    def blocked(self, username: str) -> bool:
        now = self._clock()
        self._prune(now)
        if len(self._global) >= self._global_limit:
            return True
        return len(self._failures.get(_bucket(username), ())) >= self._limit

    def record_failure(self, username: str) -> None:
        now = self._clock()
        self._prune(now)
        bucket = _bucket(username)
        self._failures.setdefault(bucket, []).append(now)
        self._global.append(now)
        if len(self._failures) > 4096:
            self._failures.pop(next(iter(self._failures)))

    def record_success(self, username: str) -> None:
        self._failures.pop(_bucket(username), None)

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        self._global = [stamp for stamp in self._global if stamp >= cutoff]
        empty: list[str] = []
        for username, stamps in self._failures.items():
            kept = [stamp for stamp in stamps if stamp >= cutoff]
            if kept:
                self._failures[username] = kept
            else:
                empty.append(username)
        for username in empty:
            self._failures.pop(username, None)


def _bucket(username: str) -> str:
    return username.strip().casefold()[:64]
