"""The small key-value surface the Cosmos-backed stores need, so their logic can be tested without Azure."""

from __future__ import annotations

from typing import Any, Protocol


class Table(Protocol):
    """Documents addressed by (pk, id). `get` returns a copy that carries an `_etag` for optimistic concurrency."""

    async def get(self, pk: str, id: str) -> dict[str, Any] | None: ...

    async def create(self, doc: dict[str, Any], ttl: int | None = None) -> bool:
        """Insert a new document. Returns False when (pk, id) already exists."""

    async def replace(self, doc: dict[str, Any], etag: str, ttl: int | None = None) -> bool:
        """Overwrite only if the stored document still has this etag. Returns False if it changed or vanished."""

    async def delete(self, pk: str, id: str) -> None: ...

    async def increment(self, pk: str, id: str, field: str, by: int, base: dict[str, Any], ttl: int) -> None:
        """Atomically add `by` to a numeric field, creating the document from `base` when it does not exist yet."""

    async def rows(self, pk: str, field: str, above: int, at_most: int | None = None, newest_first: bool = False, limit: int | None = None) -> list[dict[str, Any]]:
        """Documents in one partition whose numeric `field` is > above (and <= at_most), ordered by that field."""
