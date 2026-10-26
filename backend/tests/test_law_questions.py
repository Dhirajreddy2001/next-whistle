"""General Laws questions: the Core finds the rule, the agent explains it for this viewer, and a fixed text backs it up."""

from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from next_whistle_shared.contracts.agent import AgentResponse, LawExplanation, TokenUsage

R = "/api/matches/m_rvr_hrb/referee/questions"
T = 4_600_000


class LawAgent:
    def __init__(self):
        self.payloads = []

    async def generate(self, payload, visible=None):
        self.payloads.append(payload)
        excerpt = payload.rule_excerpts[0]
        return AgentResponse(
            request_id=payload.request_id, status="ok", source="fake", workflow_version="test", answer=f"Explained {excerpt.rule_id} for {payload.viewer.level}/{payload.viewer.language}.",
            law_explanation=LawExplanation(headline="h", answer="a", law_ids=[excerpt.rule_id]), usage=TokenUsage(input_tokens=900, output_tokens=150),
        )


def build(tmp_path: Path, agent):
    settings = replace(Settings.from_env(), session_db_path=tmp_path / "s.sqlite3", session_signing_key="k" * 40, limit_session_hour=500, limit_session_day=500, limit_ip_hour=500, limit_ip_sessions_hour=500)
    return TestClient(create_app(settings, agent_client=agent))


def ask(client, headers, text, level="beginner", language="en", cutoff=T, **extra):
    return client.post(R, headers=headers, json={"cutoffMs": cutoff, "questionText": text, "level": level, "language": language, **extra}).json()


def session(client):
    return {"Authorization": f"Bearer {client.post('/api/sessions').json()['accessToken']}"}


def test_a_question_naming_a_rule_reaches_the_explainer_with_that_rule_and_the_viewers_settings(tmp_path):
    agent = LawAgent()
    with build(tmp_path, agent) as client:
        headers = session(client)
        for text in ("can you explain when red card is given", "when is a corner awarded", "how does a free kick work"):
            body = ask(client, headers, text, level="pro", language="es")
            assert body["scope"] == "general_law" and body["source"] == "fake" and body["ruleRefs"]
    assert [p.rule_excerpts[0].rule_id for p in agent.payloads] == ["r_law12_red_card", "r_law17_corner_kick", "r_law13_free_kick"]
    assert all(p.viewer.level == "pro" and p.viewer.language == "es" and p.event_id is None for p in agent.payloads)
    assert agent.payloads[0].rule_excerpts[0].plain  # the plain-language version travels with the rule


def test_rule_answers_are_cached_per_level_and_language_for_every_viewer_and_every_time(tmp_path):
    agent = LawAgent()
    with build(tmp_path, agent) as client:
        a, b = session(client), session(client)
        first = ask(client, a, "when is a red card given", level="beginner")
        again = ask(client, b, "can you explain when red card is given", level="beginner", cutoff=T - 1_200_000)  # another viewer, 20 minutes earlier
        later = ask(client, b, "when is a red card given", level="beginner", cutoff=T + 600_000)  # and 10 minutes later
        other_level = ask(client, b, "when is a red card given", level="pro")
        other_language = ask(client, b, "when is a red card given", level="beginner", language="es")
    assert first.get("cached") is None and again["cached"] is True and later["cached"] is True and again["answer"] == first["answer"]
    assert other_level.get("cached") is None and other_language.get("cached") is None
    assert len(agent.payloads) == 3  # one call per rule, level and language, never per viewer


def test_when_the_model_is_down_the_stored_rule_text_is_served_and_not_cached(tmp_path):
    class Down(LawAgent):
        async def generate(self, payload, visible=None):
            raise ConnectionError("down")

    with build(tmp_path, Down()) as client:
        headers = session(client)
        first = ask(client, headers, "when is a red card given", level="beginner")
        second = ask(client, headers, "when is a red card given", level="beginner")
    assert first["source"] == "template" and "red card sends a player off" in first["answer"]
    assert second.get("cached") is None  # a fallback is never remembered as if it were the real answer


def test_questions_about_the_moment_on_screen_still_use_the_full_agent(tmp_path):
    agent = LawAgent()
    with build(tmp_path, agent) as client:
        headers = session(client)
        body = ask(client, headers, "Why was this offside?", eventId="e_m_rvr_hrb_01374")
    assert body["scope"] == "in_scope"
    assert all(not p.rule_excerpts for p in agent.payloads)  # that one is a decision question, not a rule explainer request


def test_greetings_get_a_friendly_pointer_in_the_viewers_language_without_a_model_call(tmp_path):
    agent = LawAgent()
    with build(tmp_path, agent) as client:
        headers = session(client)
        english = ask(client, headers, "hi")
        spanish = ask(client, headers, "hola!", language="es")
        thanks = ask(client, headers, "thank you")
    assert english["scope"] == "smalltalk" and "Decisions tab" in english["answer"]
    assert spanish["scope"] == "smalltalk" and "pestaña Decisiones" in spanish["answer"]
    assert thanks["scope"] == "smalltalk" and agent.payloads == []
