"""In-memory Table with Cosmos-like ETag semantics. `get` yields to the loop so concurrent callers genuinely interleave."""

import asyncio
import copy
import itertools
from typing import Any


class InMemoryTable:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], dict[str, Any]] = {}
        self._etags = itertools.count(1)

    async def get(self, pk, id):
        await asyncio.sleep(0)
        doc = self.docs.get((pk, id))
        return copy.deepcopy(doc) if doc else None

    def _store(self, doc, ttl):
        body = {k: v for k, v in copy.deepcopy(doc).items() if not k.startswith("_")}
        if ttl:
            body["ttl"] = ttl
        body["_etag"] = f"e{next(self._etags)}"
        self.docs[(body["pk"], body["id"])] = body

    async def create(self, doc, ttl=None):
        if (doc["pk"], doc["id"]) in self.docs:
            return False
        self._store(doc, ttl)
        return True

    async def replace(self, doc, etag, ttl=None):
        current = self.docs.get((doc["pk"], doc["id"]))
        if current is None or current["_etag"] != etag:
            return False
        self._store(doc, ttl)
        return True

    async def delete(self, pk, id):
        self.docs.pop((pk, id), None)

    async def increment(self, pk, id, field, by, base, ttl):
        current = self.docs.get((pk, id))
        if current is None:
            self._store({**base, "pk": pk, "id": id, field: by}, ttl)
        else:
            current[field] += by
            current["_etag"] = f"e{next(self._etags)}"

    async def rows(self, pk, field, above, at_most=None, newest_first=False, limit=None):
        found = [d for (p, _), d in self.docs.items() if p == pk and d[field] > above and (at_most is None or d[field] <= at_most)]
        found.sort(key=lambda d: d[field], reverse=newest_first)
        return [copy.deepcopy(d) for d in (found[:limit] if limit else found)]
