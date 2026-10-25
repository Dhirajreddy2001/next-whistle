"""Connect Core to Cosmos DB and build its stores. Run `python -m next_whistle_core.store.cosmos` once to create the database."""

from __future__ import annotations

from typing import Any

from next_whistle_core.store.aio import AsyncStore  # noqa: F401  (kept for symmetry with the SQLite wiring)
from next_whistle_core.store.cosmos_stores import CosmosOps, CosmosSessions
from next_whistle_core.store.cosmos_table import CosmosTable


async def open_cosmos(settings) -> tuple[Any, Any, list]:
    from azure.cosmos.aio import CosmosClient

    if not settings.cosmos_endpoint:
        raise RuntimeError("COSMOS_ENDPOINT is required when STORE_BACKEND=cosmos")
    closers = []
    if settings.cosmos_key:
        credential: Any = settings.cosmos_key
    else:
        from azure.identity.aio import DefaultAzureCredential  # managed identity with the Cosmos data-plane RBAC role

        credential = DefaultAzureCredential()
        closers.append(credential.close)
    client = CosmosClient(settings.cosmos_endpoint, credential)
    closers.insert(0, client.close)
    container = client.get_database_client(settings.cosmos_database).get_container_client(settings.cosmos_container)
    ops = CosmosOps(CosmosTable(container), settings.allowance_budget_units)
    await ops.ensure()
    return CosmosSessions(CosmosTable(container), ops), ops, closers


async def create_database(settings) -> None:
    """One-time setup with an account key: a database with shared throughput and one container with item-level TTL."""
    from azure.cosmos import PartitionKey
    from azure.cosmos.aio import CosmosClient

    async with CosmosClient(settings.cosmos_endpoint, settings.cosmos_key) as client:
        database = await client.create_database_if_not_exists(settings.cosmos_database, offer_throughput=400)
        await database.create_container_if_not_exists(settings.cosmos_container, partition_key=PartitionKey(path="/pk"), default_ttl=-1)
    print(f"Cosmos database '{settings.cosmos_database}' and container '{settings.cosmos_container}' are ready.")


if __name__ == "__main__":
    import asyncio

    from next_whistle_core.config import Settings

    asyncio.run(create_database(Settings.from_env()))
