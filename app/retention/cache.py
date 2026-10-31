from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any


class TTLCache:
    """Bounded process-memory cache. It is not durable across restarts."""

    def __init__(self, ttl_seconds: int = 86400, max_items: int = 1000) -> None:
        self.ttl_seconds = max(1, ttl_seconds)
        self.max_items = max(1, max_items)
        self._items: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.RLock()

    def _purge(self) -> None:
        now = time.monotonic()
        expired = [key for key, (expiry, _) in self._items.items() if expiry <= now]
        for key in expired:
            self._items.pop(key, None)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._purge()
            self._items.pop(key, None)
            self._items[key] = (time.monotonic() + self.ttl_seconds, value)
            self._purge()

    def get(self, key: str) -> Any | None:
        with self._lock:
            self._purge()
            item = self._items.get(key)
            if item is None:
                return None
            self._items.move_to_end(key)
            return item[1]

    def delete(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)

    def values(self, prefix: str) -> list[Any]:
        with self._lock:
            self._purge()
            return [value for key, (_, value) in self._items.items() if key.startswith(prefix)]

    def count(self) -> int:
        with self._lock:
            self._purge()
            return len(self._items)
