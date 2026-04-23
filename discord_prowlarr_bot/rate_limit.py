from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass(slots=True)
class RateLimiter:
    max_calls: int
    window_seconds: int
    _calls: dict[int, deque[float]] = field(default_factory=dict)

    def allow(self, user_id: int) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        timestamps = self._calls.setdefault(user_id, deque())

        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

        if len(timestamps) >= self.max_calls:
            return False

        timestamps.append(now)
        return True
