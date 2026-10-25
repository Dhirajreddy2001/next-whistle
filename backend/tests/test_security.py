"""Abuse controls: rate limits, spend allowance, concurrency, idempotency, cache safety and hardening."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from next_whistle_core.limits import Busy, GenerationGate, SlidingWindowLimiter
from next_whistle_core.store.ops import OpsStore
from next_whistle_shared.contracts.agent import AgentResponse, TokenUsage

EVENTS = ["e_m_rvr_hrb_01167", "e_m_rvr_hrb_00574", "e_m_rvr_hrb_01124"]
CUTOFF = 4_600_000
KEY = "test-signing-key-that-is-long-enough"


class StubAgent:
    def __init__(self, delay: float = 0.0, usage: tuple[int, int] = (1000, 200)):
        self.calls, self.delay, self.usage = 0, delay, usage

    async def generate(self, payload, visible=None):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return AgentResponse(
            request_id=payload.request_id, status="ok", source="fake", workflow_version="test", answer="An answer.",
            evidence_ids=[payload.event_id] if payload.event_id else [], usage=TokenUsage(input_tokens=self.usage[0], output_tokens=self.usage[1]),
        )


def build(tmp_path: Path, agent=None, **overrides):
    settings = replace(Settings.from_env(), **{"session_db_path": tmp_path / "sessions.sqlite3", "session_signing_key": KEY, **overrides})
    return TestClient(create_app(settings, agent_client=agent or StubAgent()))


def remaining(client) -> int:
    return asyncio.run(client.app.state.ops.remaining_units(10**10))


def session(client) -> dict:
    response = client.post("/api/sessions")
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['accessToken']}"}


def explain(client, headers, event=EVENTS[0], cutoff=CUTOFF, level="beginner", extra=None):
    return client.post("/api/matches/m_rvr_hrb/explanations", headers={**headers, **(extra or {})},
                       json={"cutoffMs": cutoff, "eventId": event, "intent": "explain", "level": level})


def ask(client, headers, text, **extra):
    return client.post("/api/matches/m_rvr_hrb/referee/questions", headers=headers, json={"cutoffMs": CUTOFF, "questionText": text, **extra})


# ---- sessions and per-network limits --------------------------------------------------------------------------

def test_session_creation_is_limited_per_network_with_retry_after(tmp_path):
    with build(tmp_path, limit_ip_sessions_hour=3) as client:
        for _ in range(3):
            assert client.post("/api/sessions").status_code == 201
        refused = client.post("/api/sessions")
    assert refused.status_code == 429 and refused.json()["code"] == "rate_limited"
    assert int(refused.headers["retry-after"]) >= 1
    assert refused.headers["content-type"].startswith("application/problem+json")


def test_forwarded_header_is_ignored_unless_a_trusted_proxy_is_configured(tmp_path):
    with build(tmp_path, limit_ip_sessions_hour=2) as client:
        for index in range(2):
            assert client.post("/api/sessions", headers={"X-Forwarded-For": f"10.0.0.{index}"}).status_code == 201
        assert client.post("/api/sessions", headers={"X-Forwarded-For": "10.0.0.99"}).status_code == 429
    with build(tmp_path / "proxy", limit_ip_sessions_hour=1, trust_proxy=True) as client:
        assert client.post("/api/sessions", headers={"X-Forwarded-For": "1.1.1.1, 9.9.9.1"}).status_code == 201
        assert client.post("/api/sessions", headers={"X-Forwarded-For": "2.2.2.2, 9.9.9.1"}).status_code == 429  # same proxy-seen peer
        assert client.post("/api/sessions", headers={"X-Forwarded-For": "2.2.2.2, 9.9.9.2"}).status_code == 201


def test_generation_is_limited_per_session_and_per_network(tmp_path):
    with build(tmp_path, limit_session_hour=2, limit_ip_hour=50) as client:
        headers = session(client)
        assert [explain(client, headers, event).status_code for event in EVENTS] == [200, 200, 429]
    with build(tmp_path / "ip", limit_session_hour=50, limit_ip_hour=2) as client:
        first, second = session(client), session(client)  # a new session does not escape the network limit
        assert [explain(client, first, EVENTS[0]).status_code, explain(client, second, EVENTS[1]).status_code, explain(client, second, EVENTS[2]).status_code] == [200, 200, 429]


def test_free_text_questions_weigh_double_and_template_answers_use_no_model(tmp_path):
    agent = StubAgent()
    with build(tmp_path, agent, limit_session_hour=4) as client:
        headers = session(client)
        first = ask(client, headers, "How long can a goalkeeper hold the ball?")
        second = ask(client, headers, "What happens at a corner kick?")
        third = ask(client, headers, "Who will win?")
    assert [first.status_code, second.status_code, third.status_code] == [200, 200, 429]
    assert agent.calls == 0


def test_read_endpoints_are_rate_limited_but_health_is_not(tmp_path):
    with build(tmp_path, limit_read_minute=5) as client:
        codes = [client.get("/api/matches").status_code for _ in range(7)]
        assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
        assert int(client.get("/api/matches").headers["retry-after"]) >= 1
        assert client.get("/health/live").status_code == 200


def test_oversized_request_bodies_are_rejected(tmp_path):
    with build(tmp_path, max_body_bytes=2000) as client:
        response = client.post("/api/sessions/refresh", content=b"x" * 5000, headers={"Content-Type": "application/json"})
    assert response.status_code == 413 and response.json()["code"] == "payload_too_large"


# ---- spend allowance, kill switch, concurrency -----------------------------------------------------------------

def test_allowance_is_reserved_settled_and_exhaustion_is_refused(tmp_path):
    agent = StubAgent(usage=(1000, 200))  # 1000 + 200 * 4 = 1800 units
    with build(tmp_path, agent, allowance_budget_units=41_000, allowance_reserve_units=40_000) as client:
        headers = session(client)
        assert explain(client, headers, EVENTS[0]).status_code == 200
        assert remaining(client) == 41_000 - 1_800  # settled to actual use, not the reservation
        refused = explain(client, headers, EVENTS[1])
    assert refused.status_code == 429 and refused.json()["code"] == "allowance_exhausted"
    assert agent.calls == 1


def test_a_failed_generation_releases_its_reservation(tmp_path):
    class Exploding(StubAgent):
        async def generate(self, payload, visible=None):
            raise RuntimeError("model unavailable")

    with build(tmp_path, Exploding(), allowance_budget_units=100_000) as client:
        headers = session(client)
        response = explain(client, headers)  # Core serves the factual fallback
        assert response.status_code == 200 and response.json()["status"] == "fallback"
        assert remaining(client) == 100_000


def test_kill_switch_serves_templates_without_calling_the_agent_or_using_limits(tmp_path):
    agent = StubAgent()
    with build(tmp_path, agent, generation_enabled=False, limit_session_hour=1) as client:
        headers = session(client)
        bodies = [explain(client, headers, EVENTS[index % 3], level=level).json() for index, level in enumerate(["beginner", "intermediate", "pro", "beginner"])]
    assert agent.calls == 0
    assert all(body["source"] == "template" and body["generationEnabled"] is False for body in bodies)


def test_concurrency_gate_runs_limited_slots_queues_then_refuses():
    async def scenario():
        gate = GenerationGate(concurrency=1, queue=1)
        release = asyncio.Event()

        async def hold():
            async with gate.slot():
                await release.wait()

        running = asyncio.create_task(hold())
        await asyncio.sleep(0)
        waiting = asyncio.create_task(hold())
        await asyncio.sleep(0)
        with pytest.raises(Busy):
            async with gate.slot():
                pass
        release.set()
        await asyncio.gather(running, waiting)
        async with gate.slot():  # capacity is back
            pass

    asyncio.run(scenario())


def test_busy_server_answers_503_with_retry_after(tmp_path):
    agent = StubAgent(delay=0.6)
    with build(tmp_path, agent, gen_concurrency=1, gen_queue=0, limit_session_hour=50, limit_ip_hour=50) as client:
        first, second = session(client), session(client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            slow = pool.submit(explain, client, first, EVENTS[0])
            time.sleep(0.2)  # let the first request occupy the only slot
            fast = pool.submit(explain, client, second, EVENTS[1])
            responses = [slow.result(), fast.result()]
    assert sorted(response.status_code for response in responses) == [200, 503]
    busy = next(response for response in responses if response.status_code == 503)
    assert busy.json()["code"] == "busy" and int(busy.headers["retry-after"]) >= 1
    assert remaining(client) > 0  # the refused request released its reservation


# ---- idempotency and cache ---------------------------------------------------------------------------------------

def test_idempotent_retries_return_the_same_answer_without_a_second_generation(tmp_path):
    agent = StubAgent()
    with build(tmp_path, agent) as client:
        headers = session(client)
        key = {"Idempotency-Key": "0a1b2c3d-4e5f-6789-abcd-ef0123456789"}
        first = explain(client, headers, extra=key)
        replay = explain(client, headers, extra=key)
        different = explain(client, headers, event=EVENTS[1], extra=key)
        malformed = explain(client, headers, extra={"Idempotency-Key": "bad key!"})
    assert first.status_code == replay.status_code == 200 and replay.headers["idempotent-replay"] == "true"
    assert replay.json() == first.json() and agent.calls == 1
    assert different.status_code == 422 and different.json()["code"] == "idempotency_conflict"
    assert malformed.status_code == 400


def test_cache_hits_cost_nothing_and_never_reveal_the_future(tmp_path):
    agent = StubAgent()
    with build(tmp_path, agent, limit_session_hour=2) as client:
        headers = session(client)
        assert explain(client, headers).json().get("cached") is None
        other = session(client)  # a different viewer gets the cached answer and no limit is consumed
        hit = explain(client, other)
        assert hit.json()["cached"] is True and agent.calls == 1
        assert [explain(client, other, level="beginner").status_code for _ in range(5)] == [200] * 5
        # A viewer who has seen less of the match must not receive an answer computed with more.
        earlier = explain(client, other, cutoff=CUTOFF - 5_000)
        assert earlier.json().get("cached") is None and agent.calls == 2
        later = explain(client, other, cutoff=CUTOFF + 20_000)
        assert later.json()["cached"] is True
        different_level = explain(client, other, level="pro")
        assert different_level.json().get("cached") is None  # level is part of the cache key


def test_template_answers_are_never_cached(tmp_path):
    class Offline(StubAgent):
        async def generate(self, payload, visible=None):
            raise ConnectionError("offline")

    with build(tmp_path, Offline()) as client:
        headers = session(client)
        assert explain(client, headers).json()["status"] == "fallback"
        assert explain(client, headers).json().get("cached") is None


# ---- hardening ---------------------------------------------------------------------------------------------------

def test_security_headers_are_set_and_hsts_only_in_production(tmp_path):
    with build(tmp_path) as client:
        headers = client.get("/api/matches").headers
        assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in headers["content-security-policy"] and "strict-transport-security" not in headers
    with build(tmp_path / "prod", environment="production", session_signing_key="p" * 40) as client:
        assert "max-age" in client.get("/api/matches").headers["strict-transport-security"]


def test_production_requires_a_strong_persistent_signing_key(tmp_path):
    for key in (None, "short"):
        settings = replace(Settings.from_env(), session_db_path=tmp_path / f"{key}.sqlite3", session_signing_key=key, environment="production")
        with pytest.raises(RuntimeError), TestClient(create_app(settings)):
            pass


def test_local_signing_key_persists_so_restarts_keep_sessions(tmp_path):
    settings = replace(Settings.from_env(), session_db_path=tmp_path / "sessions.sqlite3", session_signing_key=None)
    with TestClient(create_app(settings)) as first:
        headers = session(first)
    assert (tmp_path / "session.key").stat().st_mode & 0o077 == 0  # owner-only
    with TestClient(create_app(settings)) as second:
        assert second.delete("/api/sessions/current", headers=headers).status_code == 204


# ---- stores ------------------------------------------------------------------------------------------------------

def test_sliding_window_expires_and_bounds_memory():
    limiter = SlidingWindowLimiter()
    assert [limiter.allow("a", 2, 10, now)[0] for now in (0, 1, 2)] == [True, True, False]
    assert limiter.allow("a", 2, 10, 11)[0] is True  # the window has moved on
    assert limiter.allow("b", 1, 10, 0)[0] is True  # clients are independent


def test_ops_store_windows_and_stale_reservations(tmp_path):
    store = OpsStore(tmp_path / "ops.sqlite3", budget_units=100)
    assert store.consume("scope", "k", 3, 60, now=1000, weight=2) == (True, 0)
    allowed, wait = store.consume("scope", "k", 3, 60, now=1010, weight=2)
    assert allowed is False and wait == 50
    assert store.consume("scope", "k", 3, 60, now=1061, weight=2) == (True, 0)
    assert store.reserve("r1", 60, now=0) and not store.reserve("r2", 60, now=1)
    assert store.reserve("r2", 60, now=10_000)  # r1 was abandoned and expired, freeing the budget
