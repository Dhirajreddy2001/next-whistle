"""Pick the persistence backend: SQLite for local work and tests, Cosmos DB for production."""

from __future__ import annotations

from typing import Any

from next_whistle_core.store.aio import AsyncStore
from next_whistle_core.store.ops import OpsStore
from next_whistle_core.store.sessions import SQLiteSessionStore


async def open_stores(settings) -> tuple[Any, Any, list]:
    """Returns (sessions, ops, async close callbacks). Every store method is awaitable."""
    if settings.store_backend == "cosmos":
        from next_whistle_core.store.cosmos import open_cosmos

        return await open_cosmos(settings)
    return (
        AsyncStore(SQLiteSessionStore(settings.session_db_path)),
        AsyncStore(OpsStore(settings.session_db_path, settings.allowance_budget_units)),
        [],
    )
