"""Golden Q&A cases built from the real dataset, so event IDs never go stale when data is regenerated."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LEVELS = ("beginner", "intermediate", "pro")
SUPPORTED_DECISIONS = {"offside", "tactical_foul_yellow", "penalty"}
EXPLAIN_KINDS = ("goal", "goal_disallowed", "card_red", "card_yellow", "save", "substitution")


def _questions(root: Path) -> dict[str, str]:
    rows = json.loads((root / "data" / "rules" / "questions.yaml").read_text(encoding="utf-8"))
    first: dict[str, str] = {}
    for row in rows:
        first.setdefault(row["decision_type"], row["text"]["en"])
    return first


def build_cases(dataset: Any, root: Path) -> list[dict[str, Any]]:
    questions = _questions(root)
    cases: list[dict[str, Any]] = []
    for match_id, data in sorted(dataset.matches.items()):
        events = sorted(data.events, key=lambda e: e["seq"])
        for kind in EXPLAIN_KINDS:
            event = next((e for e in events if e["type"] == kind), None)
            if event is None:
                continue
            for level in LEVELS:
                cases.append(_case(f"explain.{match_id}.{kind}.{level}.en", "explain", match_id, event, level, "en"))
            cases.append(_case(f"explain.{match_id}.{kind}.beginner.es", "explain", match_id, event, "beginner", "es"))
        for event in events:
            decision = event.get("decision")
            if not decision or decision["decision_type"] not in SUPPORTED_DECISIONS:
                continue
            text = questions.get(decision["decision_type"], "Why was this decision made?")
            for level in LEVELS:
                case = _case(f"referee.{event['event_id']}.{level}.en", "referee_question", match_id, event, level, "en")
                case["question_text"] = text
                cases.append(case)
        last = events[-1]["available_ms"]
        for fraction in (2, 1):
            for level in LEVELS:
                cases.append({"case_id": f"recap.{match_id}.{level}.t{fraction}", "intent": "recap", "match_id": match_id, "event_id": None,
                              "cutoff_ms": last // fraction, "level": level, "language": "en"})
    for rule in _rules(root):
        if not rule.get("plain"):
            continue
        for level in LEVELS:
            cases.append({"case_id": f"law.{rule['rule_id']}.{level}.en", "intent": "referee_question", "match_id": "m_rvr_hrb", "event_id": None,
                          "cutoff_ms": 1_000_000, "level": level, "language": "en", "question_text": f"Can you explain {rule['law_title'].lower()}?", "rule_id": rule["rule_id"]})
        cases.append({"case_id": f"law.{rule['rule_id']}.beginner.es", "intent": "referee_question", "match_id": "m_rvr_hrb", "event_id": None,
                      "cutoff_ms": 1_000_000, "level": "beginner", "language": "es", "question_text": f"¿Puedes explicar {rule['law_title'].lower()}?", "rule_id": rule["rule_id"]})
    return cases


def _rules(root: Path) -> list[dict[str, Any]]:
    return json.loads((root / "data" / "rules" / "v1.2.0" / "rules.json").read_text(encoding="utf-8"))


def _case(case_id: str, intent: str, match_id: str, event: dict[str, Any], level: str, language: str) -> dict[str, Any]:
    return {"case_id": case_id, "intent": intent, "match_id": match_id, "event_id": event["event_id"],
            "cutoff_ms": event["available_ms"] + 1000, "level": level, "language": language}
