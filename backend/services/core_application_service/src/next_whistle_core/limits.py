"""Request identity, in-memory read limiting and the generation concurrency gate."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from collections import OrderedDict, deque
from contextlib import asynccontextmanager

from fastapi import Request

MAX_TRACKED_CLIENTS = 50_000


def client_ip(request: Request, trust_proxy: bool) -> str:
    """The caller's address. Behind one trusted proxy the proxy appends the real peer last; earlier entries can be forged."""
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        parts = [part.strip() for part in forwarded.split(",") if part.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "unknown"


def hash_ip(ip: str, key: bytes) -> str:
    """HMAC-SHA256 truncated to 16 bytes, so raw addresses are never stored or logged."""
    return hmac.new(key, ip.encode(), hashlib.sha256).digest()[:16].hex()


class SlidingWindowLimiter:
    """In-memory limiter for cheap, high-volume reads. Bounded in memory, so a flood of fake addresses cannot grow it."""

    def __init__(self) -> None:
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def allow(self, key: str, limit: int, window_s: float, now: float) -> tuple[bool, int]:
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= MAX_TRACKED_CLIENTS:
                self._hits.popitem(last=False)
            hits = self._hits[key] = deque()
        else:
            self._hits.move_to_end(key)
        while hits and hits[0] <= now - window_s:
            hits.popleft()
        if len(hits) >= limit:
            return False, max(1, int(hits[0] + window_s - now) + 1)
        hits.append(now)
        return True, 0


class Busy(Exception):
    """Every generation slot is taken and the waiting queue is full."""


class GenerationGate:
    """At most `concurrency` generations run at once; up to `queue` more wait; anything beyond that is refused."""

    def __init__(self, concurrency: int, queue: int) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._queue = queue
        self._waiting = 0

    @asynccontextmanager
    async def slot(self):
        if self._semaphore.locked():
            if self._waiting >= self._queue:
                raise Busy()
            self._waiting += 1
            try:
                await self._semaphore.acquire()
            finally:
                self._waiting -= 1
        else:
            await self._semaphore.acquire()
        try:
            yield
        finally:
            self._semaphore.release()
