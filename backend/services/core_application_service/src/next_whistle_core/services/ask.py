"""Free-text Ask the Ref handling that needs no model: refusals, redirects and general Laws answers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from next_whistle_shared.ask import is_future_question, is_injection, mentioned_decision_type
from next_whistle_shared.evidence.rule_search import RuleIndex

MIN_RULE_SCORE = 5.0
_DEICTIC_WORDS = ("this", "that", "here", "the flag", "the decision", "the call", "the ruling", "why was", "why did")

_REFUSALS = {
    "refused": {
        "en": "I can only answer questions about this match and the Laws of the Game, so I can't act on that request. Try asking why a decision was made or how a rule works.",
        "es": "Solo puedo responder preguntas sobre este partido y las Reglas de Juego, así que no puedo atender esa petición. Pregunta por qué se tomó una decisión o cómo funciona una regla.",
    },
    "out_of_scope": {
        "en": "That question is outside what I can answer. I explain recorded decisions and the Laws of the Game, and I can't predict what happens later in the match.",
        "es": "Esa pregunta queda fuera de lo que puedo responder. Explico decisiones registradas y las Reglas de Juego, y no puedo predecir lo que ocurra más adelante en el partido.",
    },
}


@dataclass(frozen=True)
class AskOutcome:
    scope: str  # refused | out_of_scope | redirect | general_law
    answer: str
    rule_refs: list[dict[str, Any]] = field(default_factory=list)
    redirect_event_id: str | None = None


@lru_cache(maxsize=4)
def load_rule_index(project_root: str, rules_version: str) -> RuleIndex:
    path = Path(project_root) / "data" / "rules" / f"v{rules_version}" / "rules.json"
    return RuleIndex(json.loads(path.read_text(encoding="utf-8")))


def _rule_ref(rule: dict[str, Any]) -> dict[str, Any]:
    return {"ruleId": rule["rule_id"], "lawNumber": rule["law_number"], "title": rule["law_title"], "sourceUrl": rule["source_url"]}


def _text(key: str, language: str) -> str:
    return _REFUSALS[key]["es" if language.startswith("es") else "en"]


def answer_without_model(
    *, question: str, language: str, selected_event: dict | None, visible_decisions: list[dict], index: RuleIndex,
) -> AskOutcome | None:
    """Return an outcome when the question needs no model, or None when it belongs to the selected decision."""
    if is_injection(question):
        return AskOutcome("refused", _text("refused", language))
    if is_future_question(question):
        return AskOutcome("out_of_scope", _text("out_of_scope", language))

    selected_type = (selected_event or {}).get("decision", {}).get("decision_type")
    mentioned = mentioned_decision_type(question)
    if mentioned and mentioned != selected_type:
        other = [event for event in visible_decisions if event["decision"]["decision_type"] == mentioned]
        if other:
            latest = max(other, key=lambda event: event["match_ms"])
            minute = latest["match_ms"] // 60_000 + 1
            return AskOutcome(
                "redirect",
                f"This question is about the {minute}' {mentioned.replace('_', ' ')} decision. Select that decision to see how the Laws apply to it.",
                redirect_event_id=latest["event_id"],
            )

    hits = index.search(question, 2)
    strong = [rule for score, rule in hits if score >= MIN_RULE_SCORE]
    about_selected = selected_type is not None and (mentioned == selected_type or any(word in question.lower() for word in _DEICTIC_WORDS))
    if selected_type and about_selected:
        return None
    if strong:
        text = " ".join(rule["summary"] for rule in strong[:1])
        return AskOutcome("general_law", f"Under the Laws of the Game: {text}", [_rule_ref(rule) for rule in strong])
    if selected_type:
        return None
    return AskOutcome("out_of_scope", _text("out_of_scope", language))
