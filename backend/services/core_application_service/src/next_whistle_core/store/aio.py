"""Async face for the stores. Core awaits every store call, so SQLite (local) and Cosmos DB (production) are interchangeable."""

from __future__ import annotations

import asyncio
from typing import Any


class AsyncStore:
    """Runs a synchronous store's public methods on a worker thread so they never block the event loop."""

    def __init__(self, store: Any):
        self._store = store

    def __getattr__(self, name: str):
        target = getattr(self._store, name)
        if not callable(target) or name.startswith("_"):
            return target

        async def call(*args: Any, **kwargs: Any):
            return await asyncio.to_thread(target, *args, **kwargs)

        return call
