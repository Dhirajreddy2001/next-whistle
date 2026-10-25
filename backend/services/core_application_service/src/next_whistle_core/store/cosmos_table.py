"""Azure Cosmos DB for NoSQL implementation of the Table surface (store/table.py)."""

from __future__ import annotations

import re
from typing import Any

from azure.core import MatchConditions
from azure.cosmos.exceptions import CosmosAccessConditionFailedError, CosmosResourceExistsError, CosmosResourceNotFoundError

_FIELD = re.compile(r"^[a-z_]+$")


def _strip(doc: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in doc.items() if not key.startswith("_")}


class CosmosTable:
    """One container, partition key `/pk`, per-item TTL via the `ttl` field (container default TTL set to -1)."""

    def __init__(self, container: Any):
        self.container = container

    async def get(self, pk: str, id: str) -> dict[str, Any] | None:
        try:
            return dict(await self.container.read_item(id, partition_key=pk))
        except CosmosResourceNotFoundError:
            return None

    async def create(self, doc: dict[str, Any], ttl: int | None = None) -> bool:
        body = _strip(doc) | ({"ttl": ttl} if ttl else {})
        try:
            await self.container.create_item(body)
            return True
        except CosmosResourceExistsError:
            return False

    async def replace(self, doc: dict[str, Any], etag: str, ttl: int | None = None) -> bool:
        body = _strip(doc) | ({"ttl": ttl} if ttl else {})
        try:
            await self.container.replace_item(doc["id"], body, etag=etag, match_condition=MatchConditions.IfNotModified)
            return True
        except (CosmosAccessConditionFailedError, CosmosResourceNotFoundError):
            return False

    async def delete(self, pk: str, id: str) -> None:
        try:
            await self.container.delete_item(id, partition_key=pk)
        except CosmosResourceNotFoundError:
            pass

    async def increment(self, pk: str, id: str, field: str, by: int, base: dict[str, Any], ttl: int) -> None:
        operations = [{"op": "incr", "path": f"/{field}", "value": by}]
        for _ in range(3):
            try:
                await self.container.patch_item(id, partition_key=pk, patch_operations=operations)
                return
            except CosmosResourceNotFoundError:
                if await self.create({**base, "pk": pk, "id": id, field: by}, ttl):
                    return

    async def rows(self, pk: str, field: str, above: int, at_most: int | None = None, newest_first: bool = False, limit: int | None = None) -> list[dict[str, Any]]:
        if not _FIELD.match(field):
            raise ValueError("Unsafe field name")
        query = f"SELECT * FROM c WHERE c.pk = @pk AND c.{field} > @above"
        parameters = [{"name": "@pk", "value": pk}, {"name": "@above", "value": above}]
        if at_most is not None:
            query += f" AND c.{field} <= @most"
            parameters.append({"name": "@most", "value": at_most})
        if newest_first or limit:
            query += f" ORDER BY c.{field} {'DESC' if newest_first else 'ASC'}"
        if limit:
            query += f" OFFSET 0 LIMIT {int(limit)}"
        return [dict(item) async for item in self.container.query_items(query=query, parameters=parameters, partition_key=pk)]
