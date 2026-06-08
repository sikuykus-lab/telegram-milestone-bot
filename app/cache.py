from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    value: T
    expires_at: float


class TTLCache(Generic[T]):
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._entry: CacheEntry[T] | None = None
        self._lock = threading.Lock()

    def get(self) -> T | None:
        with self._lock:
            if not self._entry:
                return None
            if time.time() >= self._entry.expires_at:
                self._entry = None
                return None
            return self._entry.value

    def set(self, value: T) -> None:
        with self._lock:
            self._entry = CacheEntry(value=value, expires_at=time.time() + self._ttl)


class ReadRateLimiter:
    def __init__(self, min_interval_seconds: int) -> None:
        self._min_interval = float(min_interval_seconds)
        self._last_read_at = 0.0
        self._lock = threading.Lock()

    def wait_turn(self) -> None:
        with self._lock:
            now = time.time()
            delta = now - self._last_read_at
            if delta < self._min_interval:
                time.sleep(self._min_interval - delta)
            self._last_read_at = time.time()
