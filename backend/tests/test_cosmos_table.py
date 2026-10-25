"""The SDK adapter, tested with a stub container that raises the real azure-cosmos exceptions."""

import asyncio

import pytest
from azure.core import MatchConditions
from azure.cosmos.exceptions import CosmosAccessConditionFailedError, CosmosResourceExistsError, CosmosResourceNotFoundError

from next_whistle_core.store.cosmos_table import CosmosTable


class StubContainer:
    def __init__(self):
        self.calls, self.fail = [], {}

    async def _do(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        error = self.fail.get(name)
        if error:
            self.fail[name] = None if name == "patch_item" else error
            raise error
        return {"id": "x", "_etag": "e1", "_rid": "r"}

    async def read_item(self, *a, **k): return await self._do("read_item", *a, **k)
    async def create_item(self, *a, **k): return await self._do("create_item", *a, **k)
    async def replace_item(self, *a, **k): return await self._do("replace_item", *a, **k)
    async def delete_item(self, *a, **k): return await self._do("delete_item", *a, **k)
    async def patch_item(self, *a, **k): return await self._do("patch_item", *a, **k)

    def query_items(self, **kwargs):
        self.calls.append(("query_items", (), kwargs))

        async def generator():
            yield {"id": "1"}

        return generator()


def missing(): return CosmosResourceNotFoundError(status_code=404, message="not found")
def exists(): return CosmosResourceExistsError(status_code=409, message="conflict")
def stale(): return CosmosAccessConditionFailedError(status_code=412, message="precondition")


def run(coro): return asyncio.run(coro)


def test_missing_documents_read_as_none_and_existing_ones_carry_an_etag():
    stub = StubContainer()
    table = CosmosTable(stub)
    assert run(table.get("p", "i"))["_etag"] == "e1"
    stub.fail["read_item"] = missing()
    assert run(table.get("p", "i")) is None


def test_create_reports_conflicts_and_sets_item_ttl_without_leaking_system_fields():
    stub = StubContainer()
    table = CosmosTable(stub)
    assert run(table.create({"pk": "p", "id": "i", "_etag": "old", "n": 1}, ttl=60)) is True
    body = stub.calls[-1][1][0]
    assert body == {"pk": "p", "id": "i", "n": 1, "ttl": 60}
    stub.fail["create_item"] = exists()
    assert run(table.create({"pk": "p", "id": "i"})) is False


def test_replace_is_conditional_on_the_etag():
    stub = StubContainer()
    table = CosmosTable(stub)
    assert run(table.replace({"pk": "p", "id": "i"}, "e1")) is True
    kwargs = stub.calls[-1][2]
    assert kwargs["etag"] == "e1" and kwargs["match_condition"] == MatchConditions.IfNotModified
    stub.fail["replace_item"] = stale()
    assert run(table.replace({"pk": "p", "id": "i"}, "e1")) is False
    stub.fail["replace_item"] = missing()
    assert run(table.replace({"pk": "p", "id": "i"}, "e1")) is False


def test_increment_patches_atomically_and_creates_the_document_on_first_use():
    stub = StubContainer()
    table = CosmosTable(stub)
    run(table.increment("p", "7", "n", 2, {"bucket": 7, "n": 0}, ttl=120))
    assert stub.calls[-1][2]["patch_operations"] == [{"op": "incr", "path": "/n", "value": 2}]
    stub.calls.clear()
    stub.fail["patch_item"] = missing()
    run(table.increment("p", "7", "n", 2, {"bucket": 7, "n": 0}, ttl=120))
    assert [name for name, *_ in stub.calls] == ["patch_item", "create_item"]
    assert stub.calls[-1][1][0] == {"bucket": 7, "n": 2, "pk": "p", "id": "7", "ttl": 120}


def test_delete_ignores_missing_items():
    stub = StubContainer()
    stub.fail["delete_item"] = missing()
    run(CosmosTable(stub).delete("p", "i"))


def test_rows_builds_a_single_partition_parameterised_query_and_rejects_unsafe_fields():
    stub = StubContainer()
    table = CosmosTable(stub)
    assert run(table.rows("cache:k", "cutoff_ms", above=10, at_most=99, newest_first=True, limit=1)) == [{"id": "1"}]
    kwargs = stub.calls[-1][2]
    assert kwargs["partition_key"] == "cache:k"
    assert kwargs["query"] == "SELECT * FROM c WHERE c.pk = @pk AND c.cutoff_ms > @above AND c.cutoff_ms <= @most ORDER BY c.cutoff_ms DESC OFFSET 0 LIMIT 1"
    assert {p["name"] for p in kwargs["parameters"]} == {"@pk", "@above", "@most"}
    with pytest.raises(ValueError):
        run(table.rows("p", "n; DROP", above=0))
