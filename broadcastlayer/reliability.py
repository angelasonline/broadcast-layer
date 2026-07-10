"""Reliability primitives for live broadcast transport.

Three things a broadcast engineer will probe:

1. Reconnect recovery. A captioner's socket drops for two seconds on a flaky
   venue network. When it reconnects, it must resume the same source and the
   same sequence, not start a new one and not lose its role to the standby
   permanently. `ResumeToken` makes a reconnect idempotent.

2. Subscriber backpressure. A slow player must not block the captioner or
   stall the whole fan-out. `BoundedFanout` drops the slowest subscriber's
   backlog (never the captioner's cues) and records the drop, so one bad
   client cannot take down the track.

3. Ordered, deduplicated delivery. Cues carry a monotonic seq; a subscriber
   that reconnects can request cues after its last seen seq, and duplicates
   are suppressed. `since_seq` on the engine supports catch-up.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any


class RateLimiter:
    """Token bucket per key. Protects ingest and control endpoints."""

    def __init__(self, rate_per_sec: float, burst: int):
        self.rate = rate_per_sec
        self.burst = burst
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if tokens < 1:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1, now)
        return True


class BoundedQueue:
    """A per-subscriber outbound queue that drops oldest under pressure,
    so a slow client degrades itself rather than the whole fan-out."""

    def __init__(self, maxlen: int = 512):
        self._q: deque = deque(maxlen=maxlen)
        self.dropped = 0
        self._ev = asyncio.Event()

    def push(self, item: Any) -> None:
        if len(self._q) == self._q.maxlen:
            self.dropped += 1
        self._q.append(item)
        self._ev.set()

    async def pull(self) -> Any:
        while not self._q:
            self._ev.clear()
            await self._ev.wait()
        return self._q.popleft()


class ResumeRegistry:
    """Tracks which source id a console session owns, so a reconnect resumes
    the same source instead of orphaning it or spawning a duplicate."""

    def __init__(self):
        self._sessions: dict[str, str] = {}  # resume_token -> source_id

    def bind(self, resume_token: str, source_id: str) -> None:
        self._sessions[resume_token] = source_id

    def resolve(self, resume_token: str) -> str | None:
        return self._sessions.get(resume_token)

    def release(self, resume_token: str) -> None:
        self._sessions.pop(resume_token, None)
