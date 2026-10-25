"""The same behaviour suite runs against the SQLite stores and the Cosmos-backed stores."""

import asyncio

import pytest

from memory_table import InMemoryTable
from next_whistle_core.store.aio import AsyncStore
from next_whistle_core.store.cosmos_stores import CosmosOps, CosmosSessions
from next_whistle_core.store.ops import OpsStore
from next_whistle_core.store.sessions import SQLiteSessionStore

BUDGET = 100


async def make(kind: str, tmp_path):
    if kind == "sqlite":
        return AsyncStore(SQLiteSessionStore(tmp_path / "s.sqlite3")), AsyncStore(OpsStore(tmp_path / "s.sqlite3", BUDGET))
    table = InMemoryTable()
    ops = CosmosOps(table, BUDGET)
    await ops.ensure()
    return CosmosSessions(table, ops), ops


def backend_test(body):
    @pytest.mark.parametrize("kind", ["sqlite", "cosmos"])
    def wrapper(kind, tmp_path):
        async def scenario():
            sessions, ops = await make(kind, tmp_path)
            await body(sessions, ops)

        asyncio.run(scenario())

    wrapper.__name__ = body.__name__
    return wrapper


@backend_test
async def test_session_lifecycle_rotation_and_revocation(sessions, ops):
    await sessions.create("s_1", "hash-a", now=1000, expires_at=5000)
    assert await sessions.is_active("s_1", 1001) and not await sessions.is_active("s_1", 5000)
    assert await sessions.rotate("hash-a", "hash-b", 1100, 6000) == "s_1"
    assert await sessions.rotate("hash-a", "hash-c", 1101, 6000) is None  # a used refresh token is dead
    assert await sessions.rotate("hash-b", "hash-c", 1102, 6000) == "s_1"
    await sessions.revoke("s_1")
    assert not await sessions.is_active("s_1", 1200)
    assert await sessions.rotate("hash-c", "hash-d", 1201, 6000) is None


@backend_test
async def test_two_concurrent_refreshes_with_one_token_only_one_wins(sessions, ops):
    await sessions.create("s_2", "hash-x", now=0, expires_at=9000)
    results = await asyncio.gather(*(sessions.rotate("hash-x", f"new-{i}", 10, 9000) for i in range(6)))
    assert results.count("s_2") == 1 and results.count(None) == 5


@backend_test
async def test_weighted_session_generation_limits_with_retry_after(sessions, ops):
    await sessions.create("s_3", "h3", now=0, expires_at=99999)
    assert await sessions.reserve_generation("s_3", 100, 4, 20, 2) == (True, 0)
    assert await sessions.reserve_generation("s_3", 101, 4, 20, 2) == (True, 0)
    allowed, wait = await sessions.reserve_generation("s_3", 102, 4, 20, 1)
    assert allowed is False and 1 <= wait <= 3600


@backend_test
async def test_sliding_window_blocks_then_recovers(sessions, ops):
    assert await ops.consume("ip", "k", 3, 600, 1000, 2) == (True, 0)
    allowed, wait = await ops.consume("ip", "k", 3, 600, 1010, 2)
    assert allowed is False and 1 <= wait <= 600
    assert (await ops.consume("ip", "k", 3, 600, 1000 + 700, 2))[0] is True
    assert (await ops.consume("ip", "other", 3, 600, 1010, 3))[0] is True  # keys are independent


@backend_test
async def test_allowance_reserve_settle_release_and_stale_expiry(sessions, ops):
    assert await ops.reserve("r1", 60, 0) and not await ops.reserve("r2", 60, 1)
    await ops.settle("r1", 25)
    assert await ops.remaining_units(2) == BUDGET - 25  # settled to actual use, not the reservation
    assert await ops.reserve("r3", 70, 3)
    await ops.release("r3")
    assert await ops.remaining_units(4) == BUDGET - 25
    assert await ops.reserve("abandoned", 70, 10)
    assert await ops.reserve("later", 70, 10_000)  # the abandoned reservation expired
    await ops.settle("later", 10_000)  # spend is capped at what was reserved
    assert await ops.remaining_units(10_001) == BUDGET - 25 - 70


@backend_test
async def test_concurrent_reservations_never_overspend(sessions, ops):
    outcomes = await asyncio.gather(*(ops.reserve(f"c{i}", 30, 0) for i in range(12)))
    assert sum(outcomes) == 3  # 100 // 30


@backend_test
async def test_idempotency_states(sessions, ops):
    assert await ops.idempotency_begin("key-1", "s", "h1", 0) == ("new", None)
    assert await ops.idempotency_begin("key-1", "s", "h1", 1) == ("in_progress", None)
    assert (await ops.idempotency_begin("key-1", "s", "other", 1))[0] == "conflict"
    assert (await ops.idempotency_begin("key-1", "someone-else", "h1", 1))[0] == "conflict"
    await ops.idempotency_done("key-1", {"answer": "x"})
    assert await ops.idempotency_begin("key-1", "s", "h1", 2) == ("done", {"answer": "x"})
    assert await ops.idempotency_begin("key-2", "s", "h", 0) == ("new", None)
    await ops.idempotency_fail("key-2")
    assert await ops.idempotency_begin("key-2", "s", "h", 1) == ("new", None)  # a failed attempt can be retried


@backend_test
async def test_cache_never_serves_an_answer_computed_after_the_viewers_time(sessions, ops):
    await ops.cache_put("k", 100_000, {"answer": "a"}, 0)
    assert await ops.cache_get("k", 99_999, 1) is None
    assert await ops.cache_get("k", 100_000, 1) == {"answer": "a"}
    assert await ops.cache_get("k", 160_000, 1) == {"answer": "a"}
    assert await ops.cache_get("k", 160_001, 1) is None
    await ops.cache_put("k", 130_000, {"answer": "b"}, 0)
    assert await ops.cache_get("k", 140_000, 1) == {"answer": "b"}  # newest at or before the viewer's time
    assert await ops.cache_get("k", 120_000, 1) == {"answer": "a"}
    assert await ops.cache_get("other", 100_000, 1) is None


def test_cosmos_backend_without_an_endpoint_refuses_to_start(tmp_path):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    settings = replace(Settings.from_env(), session_db_path=tmp_path / "s.sqlite3", session_signing_key="k" * 40, store_backend="cosmos", cosmos_endpoint=None)
    with pytest.raises(RuntimeError, match="COSMOS_ENDPOINT"), TestClient(create_app(settings)):
        pass
