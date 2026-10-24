"""SQLite persistence for anonymous session refresh-token rotation."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator


class SQLiteSessionStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    refresh_hash TEXT NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS generation_usage (
                    usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    used_at INTEGER NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level="IMMEDIATE")
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create(self, session_id: str, refresh_hash: str, now: int, expires_at: int) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ? OR revoked = 1", (now,))
            connection.execute(
                "INSERT INTO sessions(session_id, refresh_hash, created_at, last_seen_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, refresh_hash, now, now, expires_at),
            )

    def rotate(self, old_hash: str, new_hash: str, now: int, expires_at: int) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT session_id FROM sessions WHERE refresh_hash = ? AND revoked = 0 AND expires_at > ?",
                (old_hash, now),
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                "UPDATE sessions SET refresh_hash = ?, last_seen_at = ?, expires_at = ? WHERE session_id = ? AND refresh_hash = ? AND revoked = 0",
                (new_hash, now, expires_at, row["session_id"], old_hash),
            )
            return row["session_id"] if updated.rowcount == 1 else None

    def is_active(self, session_id: str, now: int) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM sessions WHERE session_id = ? AND revoked = 0 AND expires_at > ?",
                (session_id, now),
            ).fetchone()
        return row is not None

    def revoke(self, session_id: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE sessions SET revoked = 1 WHERE session_id = ?", (session_id,))

    def reserve_generation(self, session_id: str, now: int, per_hour: int = 8, per_day: int = 20) -> bool:
        """Atomically enforce the public per-session generation limits."""
        with self._connection() as connection:
            connection.execute("DELETE FROM generation_usage WHERE used_at <= ?", (now - 24 * 60 * 60,))
            hourly = connection.execute(
                "SELECT COUNT(*) FROM generation_usage WHERE session_id = ? AND used_at > ?",
                (session_id, now - 60 * 60),
            ).fetchone()[0]
            daily = connection.execute(
                "SELECT COUNT(*) FROM generation_usage WHERE session_id = ? AND used_at > ?",
                (session_id, now - 24 * 60 * 60),
            ).fetchone()[0]
            if hourly >= per_hour or daily >= per_day:
                return False
            connection.execute(
                "INSERT INTO generation_usage(session_id, used_at) VALUES (?, ?)",
                (session_id, now),
            )
            return True
