"""The stored fallback text reads as plain sentences with real names, in English and Spanish, and never leaks internal ids."""

import re
from pathlib import Path

import pytest

from next_whistle_core.services.generation import referee_fallback
from next_whistle_core.services.narration import describe_event, explain_fallback, score_at
from next_whistle_core.store.datasets import load_dataset

DATASET = load_dataset(Path(__file__).resolve().parents[2] / "data" / "datasets" / "v1.3.0")
DATA = DATASET.matches["m_rvr_hrb"]
INTERNAL = re.compile(r"\b(?:e|p|t|m|s|r)_[a-z0-9_]{3,}\b|\bnull\b|None|\{|\}")


def first(kind: str) -> dict:
    return next(e for e in DATA.events if e["type"] == kind)


def decision(kind: str) -> dict:
    return next(e for e in DATA.events if e.get("decision") and e["decision"]["decision_type"] == kind)


def test_a_goal_names_the_scorer_the_team_the_provider_of_the_pass_and_the_score():
    goal = first("goal")
    text = describe_event(DATA, goal, "en", goal["available_ms"])
    assert "Mara Voss scored for Veldmoor FC, set up by Sofia Marin." in text
    assert text.endswith("It is Veldmoor FC 1–0 Tarnhaven Town.")
    spanish = describe_event(DATA, goal, "es", goal["available_ms"])
    assert "Mara Voss marcó para Veldmoor FC, tras un pase de Sofia Marin." in spanish and "Marcador: Veldmoor FC 1–0" in spanish


@pytest.mark.parametrize("kind", ["goal", "goal_disallowed", "offside", "card_yellow", "card_red", "save", "shot", "substitution", "carry", "pass"])
@pytest.mark.parametrize("language", ["en", "es"])
def test_every_event_type_reads_as_a_sentence_without_internal_names(kind, language):
    event = first(kind)
    text = describe_event(DATA, event, language, event["available_ms"])
    assert text and not INTERNAL.search(text), text


def test_the_score_ignores_a_goal_that_was_ruled_out_and_only_counts_what_was_visible():
    disallowed = first("goal_disallowed")
    before = score_at(DATA, disallowed["available_ms"] - 1)
    after = score_at(DATA, disallowed["available_ms"])
    assert sum(before.values()) == sum(after.values()) + 1


def test_a_recap_gives_the_score_and_the_latest_key_moments_in_either_language():
    cutoff = 3_700_000
    english = explain_fallback(DATA, cutoff, "en", None)
    assert english.startswith("At 62' it is Veldmoor FC 1–1 Tarnhaven Town. Latest: ")
    assert "Nia Cole scored for Tarnhaven Town, set up by Jalen Moss." in english
    spanish = explain_fallback(DATA, cutoff, "es", None)
    assert "Lo último:" in spanish and not INTERNAL.search(spanish) and not INTERNAL.search(english)
    assert "Nothing major" in explain_fallback(DATA, 5_000, "en", None)


@pytest.mark.parametrize("kind", ["offside", "penalty", "tactical_foul_yellow"])
@pytest.mark.parametrize("language", ["en", "es"])
def test_the_decision_fallback_names_the_people_involved_and_adds_the_rule(kind, language):
    event = decision(kind)
    text, refs = referee_fallback(DATA, event, language, "beginner")
    assert not INTERNAL.search(text), text
    assert refs and refs[0]["lawNumber"]
    assert "a player" not in text and "a a " not in text
    names = [p["name"] for side in (DATA.match["home"], DATA.match["away"]) for p in side["squad"] if p["player_id"] in {event["player_id"], event.get("target_player_id")}]
    assert any(name in text for name in names)
    if language == "es":
        assert "Law" not in text and "The" not in text.split(".")[0]
