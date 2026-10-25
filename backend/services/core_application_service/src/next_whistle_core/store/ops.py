"""SQLite stores for abuse control: rate windows, the spend allowance, idempotency records and the answer cache.

Everything is guarded by short immediate transactions so concurrent requests cannot overspend a limit.
"""

from __future__ import annotations

import json
from typing import Any

from next_whistle_core.store.sessions import SQLiteBase

RESERVATION_TTL_SECONDS = 300
IDEMPOTENCY_TTL_SECONDS = 24 * 3600
CACHE_TTL_SECONDS = 24 * 3600
CACHE_MAX_ROWS = 2000
CACHE_CUTOFF_WINDOW_MS = 60_000


class OpsStore(SQLiteBase):
    def __init__(self, path, budget_units: int):
        super().__init__(path)
        with self._connection() as c:
            c.execute("CREATE TABLE IF NOT EXISTS rate_events (scope TEXT NOT NULL, key TEXT NOT NULL, at INTEGER NOT NULL, weight INTEGER NOT NULL DEFAULT 1)")
            c.execute("CREATE INDEX IF NOT EXISTS rate_events_lookup ON rate_events(scope, key, at)")
            c.execute("CREATE TABLE IF NOT EXISTS allowance (id INTEGER PRIMARY KEY CHECK (id = 1), budget_units INTEGER NOT NULL, spent_units INTEGER NOT NULL DEFAULT 0)")
            c.execute("CREATE TABLE IF NOT EXISTS reservations (request_id TEXT PRIMARY KEY, units INTEGER NOT NULL, created_at INTEGER NOT NULL, state TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS idempotency (key TEXT PRIMARY KEY, session_id TEXT NOT NULL, request_hash TEXT NOT NULL, state TEXT NOT NULL, response TEXT, created_at INTEGER NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS answer_cache (cache_key TEXT NOT NULL, cutoff_ms INTEGER NOT NULL, response TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY (cache_key, cutoff_ms))")
            c.execute("INSERT OR IGNORE INTO allowance(id, budget_units) VALUES (1, ?)", (budget_units,))

    # ---- sliding-window rate limits -----------------------------------------------------------------------

    def consume(self, scope: str, key: str, limit: int, window_s: int, now: int, weight: int = 1) -> tuple[bool, int]:
        """Record `weight` events if the window has room. Returns (allowed, seconds until the oldest event expires)."""
        with self._connection() as c:
            c.execute("DELETE FROM rate_events WHERE at <= ?", (now - 24 * 3600,))
            used = c.execute("SELECT COALESCE(SUM(weight), 0) FROM rate_events WHERE scope = ? AND key = ? AND at > ?", (scope, key, now - window_s)).fetchone()[0]
            if used + weight > limit:
                oldest = c.execute("SELECT MIN(at) FROM rate_events WHERE scope = ? AND key = ? AND at > ?", (scope, key, now - window_s)).fetchone()[0] or now
                return False, max(1, oldest + window_s - now)
            c.execute("INSERT INTO rate_events(scope, key, at, weight) VALUES (?, ?, ?, ?)", (scope, key, now, weight))
            return True, 0

    # ---- spend allowance ----------------------------------------------------------------------------------

    def remaining_units(self, now: int) -> int:
        with self._connection() as c:
            return self._remaining(c, now)

    def _remaining(self, c, now: int) -> int:
        c.execute("DELETE FROM reservations WHERE state = 'reserved' AND created_at <= ?", (now - RESERVATION_TTL_SECONDS,))
        budget, spent = c.execute("SELECT budget_units, spent_units FROM allowance WHERE id = 1").fetchone()
        reserved = c.execute("SELECT COALESCE(SUM(units), 0) FROM reservations WHERE state = 'reserved'").fetchone()[0]
        return budget - spent - reserved

    def reserve(self, request_id: str, units: int, now: int) -> bool:
        with self._connection() as c:
            if self._remaining(c, now) < units:
                return False
            c.execute("INSERT OR REPLACE INTO reservations(request_id, units, created_at, state) VALUES (?, ?, ?, 'reserved')", (request_id, units, now))
            return True

    def settle(self, request_id: str, actual_units: int) -> None:
        """Turn a reservation into real spend (capped at what was reserved) and free the remainder."""
        with self._connection() as c:
            row = c.execute("SELECT units FROM reservations WHERE request_id = ? AND state = 'reserved'", (request_id,)).fetchone()
            if row is None:
                return
            c.execute("UPDATE allowance SET spent_units = spent_units + ? WHERE id = 1", (max(0, min(actual_units, row["units"])),))
            c.execute("DELETE FROM reservations WHERE request_id = ?", (request_id,))

    def release(self, request_id: str) -> None:
        with self._connection() as c:
            c.execute("DELETE FROM reservations WHERE request_id = ?", (request_id,))

    # ---- idempotency --------------------------------------------------------------------------------------

    def idempotency_begin(self, key: str, session_id: str, request_hash: str, now: int) -> tuple[str, dict[str, Any] | None]:
        """Returns ("new" | "in_progress" | "done" | "conflict", stored response)."""
        with self._connection() as c:
            c.execute("DELETE FROM idempotency WHERE created_at <= ?", (now - IDEMPOTENCY_TTL_SECONDS,))
            row = c.execute("SELECT session_id, request_hash, state, response FROM idempotency WHERE key = ?", (key,)).fetchone()
            if row is None:
                c.execute("INSERT INTO idempotency(key, session_id, request_hash, state, created_at) VALUES (?, ?, ?, 'in_progress', ?)", (key, session_id, request_hash, now))
                return "new", None
            if row["session_id"] != session_id or row["request_hash"] != request_hash:
                return "conflict", None
            if row["state"] == "done":
                return "done", json.loads(row["response"])
            return "in_progress", None

    def idempotency_done(self, key: str, response: dict[str, Any]) -> None:
        with self._connection() as c:
            c.execute("UPDATE idempotency SET state = 'done', response = ? WHERE key = ?", (json.dumps(response), key))

    def idempotency_fail(self, key: str) -> None:
        with self._connection() as c:
            c.execute("DELETE FROM idempotency WHERE key = ? AND state = 'in_progress'", (key,))

    # ---- answer cache -------------------------------------------------------------------------------------

    def cache_get(self, cache_key: str, cutoff_ms: int, now: int, window_ms: int | None = CACHE_CUTOFF_WINDOW_MS) -> dict[str, Any] | None:
        """Newest item computed at or before the viewer's cutoff, so a cached item can never reveal the future.

        `window_ms` limits how far back it may have been computed; None accepts any earlier time (used for items that
        describe one fixed moment, such as a brief about a single event).
        """
        lowest = cutoff_ms - window_ms if window_ms is not None else -1
        with self._connection() as c:
            row = c.execute(
                "SELECT response FROM answer_cache WHERE cache_key = ? AND cutoff_ms <= ? AND cutoff_ms >= ? AND created_at > ? ORDER BY cutoff_ms DESC LIMIT 1",
                (cache_key, cutoff_ms, lowest, now - CACHE_TTL_SECONDS),
            ).fetchone()
            return json.loads(row["response"]) if row else None

    def cache_put(self, cache_key: str, cutoff_ms: int, response: dict[str, Any], now: int) -> None:
        with self._connection() as c:
            c.execute("INSERT OR REPLACE INTO answer_cache(cache_key, cutoff_ms, response, created_at) VALUES (?, ?, ?, ?)", (cache_key, cutoff_ms, json.dumps(response), now))
            c.execute("DELETE FROM answer_cache WHERE created_at <= ?", (now - CACHE_TTL_SECONDS,))
            c.execute("DELETE FROM answer_cache WHERE rowid IN (SELECT rowid FROM answer_cache ORDER BY created_at DESC LIMIT -1 OFFSET ?)", (CACHE_MAX_ROWS,))
