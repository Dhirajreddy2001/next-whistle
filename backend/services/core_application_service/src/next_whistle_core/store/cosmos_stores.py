"""Sessions and abuse-control state on a Cosmos-style key-value table (see store/table.py).

Every write that must not race (the spend allowance, refresh rotation) is guarded by an ETag and retried; Cosmos
per-item TTL removes expired sessions, rate buckets, idempotency records and cached answers on its own.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from next_whistle_core.store.ops import (
    CACHE_CUTOFF_WINDOW_MS, CACHE_TTL_SECONDS, IDEMPOTENCY_TTL_SECONDS, RESERVATION_TTL_SECONDS,
)
from next_whistle_core.store.table import Table

MAX_RETRIES = 8
ALLOWANCE_PK = "allowance"


def _bucket_seconds(window_s: int) -> int:
    return 60 if window_s <= 3600 else 3600


class CosmosSessions:
    def __init__(self, table: Table, limiter: "CosmosOps"):
        self.table, self.limiter = table, limiter

    @staticmethod
    def _ttl(now: int, expires_at: int) -> int:
        return max(60, expires_at - now + 3600)

    async def create(self, session_id: str, refresh_hash: str, now: int, expires_at: int) -> None:
        ttl = self._ttl(now, expires_at)
        doc = {"pk": f"session:{session_id}", "id": "doc", "session_id": session_id, "refresh_hash": refresh_hash,
               "created_at": now, "last_seen_at": now, "expires_at": expires_at, "revoked": False}
        await self.table.create(doc, ttl)
        await self.table.create({"pk": f"refresh:{refresh_hash}", "id": "ptr", "session_id": session_id}, ttl)

    async def rotate(self, old_hash: str, new_hash: str, now: int, expires_at: int) -> str | None:
        pointer = await self.table.get(f"refresh:{old_hash}", "ptr")
        if pointer is None:
            return None
        session = await self.table.get(f"session:{pointer['session_id']}", "doc")
        if session is None or session["revoked"] or session["expires_at"] <= now or session["refresh_hash"] != old_hash:
            return None
        updated = {**session, "refresh_hash": new_hash, "last_seen_at": now, "expires_at": expires_at}
        ttl = self._ttl(now, expires_at)
        # Two concurrent refreshes with the same token race on this write; exactly one wins.
        if not await self.table.replace(updated, session["_etag"], ttl):
            return None
        await self.table.create({"pk": f"refresh:{new_hash}", "id": "ptr", "session_id": session["session_id"]}, ttl)
        await self.table.delete(f"refresh:{old_hash}", "ptr")
        return session["session_id"]

    async def is_active(self, session_id: str, now: int) -> bool:
        session = await self.table.get(f"session:{session_id}", "doc")
        return session is not None and not session["revoked"] and session["expires_at"] > now

    async def revoke(self, session_id: str) -> None:
        for _ in range(MAX_RETRIES):
            session = await self.table.get(f"session:{session_id}", "doc")
            if session is None or session["revoked"]:
                return
            if await self.table.replace({**session, "revoked": True}, session["_etag"], 3600):
                return

    async def reserve_generation(self, session_id: str, now: int, per_hour: int = 8, per_day: int = 20, weight: int = 1) -> tuple[bool, int]:
        return await self.limiter.reserve_generation(session_id, now, per_hour, per_day, weight)


class CosmosOps:
    def __init__(self, table: Table, budget_units: int):
        self.table, self.budget_units = table, budget_units

    async def ensure(self) -> None:
        await self.table.create({"pk": ALLOWANCE_PK, "id": "allowance", "budget_units": self.budget_units, "spent_units": 0, "reservations": {}})

    # ---- sliding-window limits (minute buckets up to an hour, hour buckets beyond) ---------------------------

    async def _usage(self, scope: str, key: str, window_s: int, now: int) -> tuple[int, int | None]:
        size = _bucket_seconds(window_s)
        rows = await self.table.rows(f"rate:{scope}:{key}", "bucket", above=(now - window_s) // size - 1)
        total = sum(row["n"] for row in rows)
        oldest = min((row["bucket"] for row in rows), default=None)
        return total, (oldest * size if oldest is not None else None)

    async def check(self, scope: str, key: str, limit: int, window_s: int, now: int, weight: int = 1) -> tuple[bool, int]:
        used, oldest = await self._usage(scope, key, window_s, now)
        if used + weight > limit:
            return False, max(1, (oldest or now) + window_s - now)
        return True, 0

    async def add(self, scope: str, key: str, window_s: int, now: int, weight: int = 1) -> None:
        size = _bucket_seconds(window_s)
        bucket = now // size
        await self.table.increment(f"rate:{scope}:{key}", str(bucket), "n", weight, {"bucket": bucket, "n": 0}, window_s + size)

    async def consume(self, scope: str, key: str, limit: int, window_s: int, now: int, weight: int = 1) -> tuple[bool, int]:
        allowed, wait = await self.check(scope, key, limit, window_s, now, weight)
        if allowed:
            await self.add(scope, key, window_s, now, weight)
        return allowed, wait

    async def reserve_generation(self, session_id: str, now: int, per_hour: int, per_day: int, weight: int = 1) -> tuple[bool, int]:
        hour_ok, hour_wait = await self.check("gen_hour", session_id, per_hour, 3600, now, weight)
        day_ok, day_wait = await self.check("gen_day", session_id, per_day, 86400, now, weight)
        if not hour_ok or not day_ok:
            return False, max(hour_wait, day_wait)
        await self.add("gen_hour", session_id, 3600, now, weight)
        await self.add("gen_day", session_id, 86400, now, weight)
        return True, 0

    # ---- spend allowance (one document, ETag-guarded) -----------------------------------------------------

    async def _mutate(self, change: Callable[[dict[str, Any]], tuple[Any, bool]]) -> Any:
        for _ in range(MAX_RETRIES):
            doc = await self.table.get(ALLOWANCE_PK, "allowance")
            if doc is None:
                await self.ensure()
                continue
            result, changed = change(doc)
            if not changed or await self.table.replace(doc, doc["_etag"]):
                return result
        raise RuntimeError("The spend allowance is too contended to update; the request is refused.")

    @staticmethod
    def _live(doc: dict[str, Any], now: int) -> int:
        doc["reservations"] = {rid: r for rid, r in doc["reservations"].items() if r["at"] > now - RESERVATION_TTL_SECONDS}
        return doc["budget_units"] - doc["spent_units"] - sum(r["units"] for r in doc["reservations"].values())

    async def remaining_units(self, now: int) -> int:
        doc = await self.table.get(ALLOWANCE_PK, "allowance")
        return self._live(doc, now) if doc else self.budget_units

    async def reserve(self, request_id: str, units: int, now: int) -> bool:
        def change(doc):
            if self._live(doc, now) < units:
                return False, False
            doc["reservations"][request_id] = {"units": units, "at": now}
            return True, True

        return await self._mutate(change)

    async def settle(self, request_id: str, actual_units: int) -> None:
        def change(doc):
            reservation = doc["reservations"].pop(request_id, None)
            if reservation is None:
                return None, False
            doc["spent_units"] += max(0, min(actual_units, reservation["units"]))
            return None, True

        await self._mutate(change)

    async def release(self, request_id: str) -> None:
        await self._mutate(lambda doc: (None, doc["reservations"].pop(request_id, None) is not None))

    # ---- idempotency ----------------------------------------------------------------------------------------

    async def idempotency_begin(self, key: str, session_id: str, request_hash: str, now: int) -> tuple[str, dict[str, Any] | None]:
        doc = {"pk": f"idem:{key}", "id": "doc", "session_id": session_id, "request_hash": request_hash, "state": "in_progress", "response": None}
        if await self.table.create(doc, IDEMPOTENCY_TTL_SECONDS):
            return "new", None
        row = await self.table.get(f"idem:{key}", "doc")
        if row is None:
            return await self.idempotency_begin(key, session_id, request_hash, now)
        if row["session_id"] != session_id or row["request_hash"] != request_hash:
            return "conflict", None
        return ("done", json.loads(row["response"])) if row["state"] == "done" else ("in_progress", None)

    async def idempotency_done(self, key: str, response: dict[str, Any]) -> None:
        for _ in range(MAX_RETRIES):
            row = await self.table.get(f"idem:{key}", "doc")
            if row is None or await self.table.replace({**row, "state": "done", "response": json.dumps(response)}, row["_etag"], IDEMPOTENCY_TTL_SECONDS):
                return

    async def idempotency_fail(self, key: str) -> None:
        row = await self.table.get(f"idem:{key}", "doc")
        if row is not None and row["state"] == "in_progress":
            await self.table.delete(f"idem:{key}", "doc")

    # ---- answer cache ---------------------------------------------------------------------------------------

    async def cache_get(self, cache_key: str, cutoff_ms: int, now: int) -> dict[str, Any] | None:
        """Newest answer computed at or before the viewer's cutoff (within a minute), so the cache cannot reveal the future."""
        rows = await self.table.rows(f"cache:{cache_key}", "cutoff_ms", above=cutoff_ms - CACHE_CUTOFF_WINDOW_MS - 1, at_most=cutoff_ms, newest_first=True, limit=1)
        return json.loads(rows[0]["response"]) if rows else None

    async def cache_put(self, cache_key: str, cutoff_ms: int, response: dict[str, Any], now: int) -> None:
        pk = f"cache:{cache_key}"
        doc = {"pk": pk, "id": str(cutoff_ms), "cutoff_ms": cutoff_ms, "response": json.dumps(response)}
        if not await self.table.create(doc, CACHE_TTL_SECONDS):
            existing = await self.table.get(pk, str(cutoff_ms))
            if existing is not None:
                await self.table.replace(doc, existing["_etag"], CACHE_TTL_SECONDS)
