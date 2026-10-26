"""The Core stores the level-free evidence brief and hands it back so only the Storyteller has to run."""

import hashlib
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from next_whistle_shared.contracts.agent import AgentResponse, EvidenceBrief, EvidenceFact, Significance, TokenUsage

EVENT = "e_m_rvr_hrb_01290"
SUGGESTED = "q_off_why"
T = 4_600_000


class RecordingAgent:
    """Behaves like the real agent: builds a brief on a miss, reuses a supplied one, and records what it was sent."""

    def __init__(self):
        self.payloads = []

    async def generate(self, payload, visible=None):
        self.payloads.append(payload)
        cached = payload.cached_brief is not None
        brief = payload.cached_brief or EvidenceBrief(
            brief_id="b_" + hashlib.sha256(payload.request_id.encode()).hexdigest()[:16], intent=payload.intent, event_id=payload.event_id,
            facts=[EvidenceFact(id="f1", text="Something was recorded.", evidence_ids=[payload.event_id or EVENT])],
            significance=Significance(established=False),
        )
        return AgentResponse(
            request_id=payload.request_id, status="ok", source="fake", workflow_version="test", answer=f"Answer for {payload.viewer.level}/{payload.viewer.language}.",
            evidence_ids=[payload.event_id] if payload.event_id else [], evidence_brief=brief,
            brief_cutoff_ms=payload.brief_cutoff_ms if cached else payload.cutoff_ms, brief_cached=cached,
            usage=TokenUsage(input_tokens=100, output_tokens=20),
        )


def build(tmp_path: Path, agent):
    settings = replace(Settings.from_env(), session_db_path=tmp_path / "s.sqlite3", session_signing_key="k" * 40, limit_session_hour=500, limit_session_day=500, limit_ip_hour=500, limit_ip_sessions_hour=500)
    return TestClient(create_app(settings, agent_client=agent))


def session(client):
    return {"Authorization": f"Bearer {client.post('/api/sessions').json()['accessToken']}"}


def explain(client, headers, cutoff=T, level="beginner", language="en", event=EVENT, intent="explain"):
    body = {"cutoffMs": cutoff, "intent": intent, "level": level, "language": language}
    if event:
        body["eventId"] = event
    return client.post("/api/matches/m_rvr_hrb/explanations", headers=headers, json=body)


def referee(client, headers, level="beginner", **extra):
    return client.post("/api/matches/m_rvr_hrb/referee/questions", headers=headers, json={"cutoffMs": T, "eventId": EVENT, "level": level, **extra})


def test_a_new_level_or_language_reuses_the_brief_and_only_the_storyteller_runs(tmp_path):
    agent = RecordingAgent()
    with build(tmp_path, agent) as client:
        headers = session(client)
        assert explain(client, headers, level="beginner").status_code == 200
        assert explain(client, headers, level="pro").status_code == 200
        assert explain(client, headers, level="beginner", language="es").status_code == 200
        assert explain(client, headers, level="beginner").json()["cached"] is True  # the identical request hits the answer cache
    assert agent.payloads[0].cached_brief is None
    assert [p.cached_brief is not None for p in agent.payloads] == [False, True, True]
    assert all(p.brief_cutoff_ms == T for p in agent.payloads[1:])


def test_a_brief_is_never_served_to_a_viewer_who_has_seen_less_but_reaches_any_later_viewer(tmp_path):
    agent = RecordingAgent()
    with build(tmp_path, agent) as client:
        headers = session(client)
        explain(client, headers)  # the brief is computed at T
        explain(client, headers, cutoff=T - 5_000, level="pro")  # an earlier viewer must not get it
        explain(client, headers, cutoff=T + 900_000, level="intermediate")  # a viewer 15 minutes later can: the event is fixed
    assert [p.cached_brief is not None for p in agent.payloads] == [False, False, True]


def test_match_so_far_briefs_expire_after_a_minute_because_the_score_moves(tmp_path):
    agent = RecordingAgent()
    with build(tmp_path, agent) as client:
        headers = session(client)
        explain(client, headers, event=None, intent="recap")
        explain(client, headers, event=None, intent="recap", cutoff=T + 30_000, level="pro")
        explain(client, headers, event=None, intent="recap", cutoff=T + 200_000, level="intermediate")
    assert [p.cached_brief is not None for p in agent.payloads] == [False, True, False]


def test_suggested_referee_questions_share_a_brief_and_free_text_never_does(tmp_path):
    agent = RecordingAgent()
    with build(tmp_path, agent) as client:
        headers = session(client)
        referee(client, headers, level="beginner", questionId=SUGGESTED)
        referee(client, headers, level="pro", questionId=SUGGESTED)
        referee(client, headers, level="pro", questionId="q_off_review")  # a different question needs its own brief
        referee(client, headers, level="beginner", questionText="Why was this goal ruled out?")
        referee(client, headers, level="pro", questionText="Why was this goal ruled out?")
    assert [p.cached_brief is not None for p in agent.payloads] == [False, True, False, False, False]


def test_failed_or_template_answers_never_store_a_brief(tmp_path):
    class Offline(RecordingAgent):
        async def generate(self, payload, visible=None):
            raise ConnectionError("offline")

    with build(tmp_path, Offline()) as client:
        headers = session(client)
        assert explain(client, headers).json()["status"] == "fallback"
        assert client.app.state.agent_client is not None
    agent = RecordingAgent()
    with build(tmp_path / "again", agent) as client:
        headers = session(client)
        explain(client, headers, level="pro")
    assert agent.payloads[0].cached_brief is None  # nothing was stored by the earlier failure


def test_the_brief_key_has_no_level_or_language(tmp_path):
    with build(tmp_path, RecordingAgent()) as client:
        headers = session(client)
        explain(client, headers, level="pro", language="es")
        keys = [row[0] for row in __import__("sqlite3").connect(tmp_path / "s.sqlite3").execute("select cache_key from answer_cache")]
    brief_keys = [k for k in keys if k.startswith("brief|")]
    assert len(brief_keys) == 1 and "pro" not in brief_keys[0].split("|") and "es" not in brief_keys[0].split("|")


def test_answers_and_briefs_without_evidence_are_never_cached(tmp_path):
    class Hollow(RecordingAgent):
        async def generate(self, payload, visible=None):
            response = await super().generate(payload, visible)
            empty_brief = response.evidence_brief.model_copy(update={"facts": []})
            return response.model_copy(update={"evidence_ids": [], "evidence_brief": empty_brief})

    agent = Hollow()
    with build(tmp_path, agent) as client:
        headers = session(client)
        first = explain(client, headers, level="beginner")
        second = explain(client, headers, level="beginner")
        third = explain(client, headers, level="pro")
    assert first.status_code == second.status_code == 200 and second.json().get("cached") is None
    assert [p.cached_brief is not None for p in agent.payloads] == [False, False, False]  # nothing was stored, so nothing was reused
