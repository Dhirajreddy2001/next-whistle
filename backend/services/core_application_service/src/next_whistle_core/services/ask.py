"""Free-text Ask the Ref handling that needs no model: refusals, redirects and general Laws answers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from next_whistle_shared.ask import (
    is_definition_question, is_future_question, is_injection, mentioned_decision_type, refers_to_selected_moment,
)
from next_whistle_shared.evidence.rule_search import RuleIndex

MIN_RULE_SCORE = 5.0
# Rule tags that belong to each kind of recorded decision. A strong rule match in the selected decision's own area
# means the viewer is asking about that decision, not about the Laws in general.
_DECISION_TAGS = {
    "offside": {"offside", "goal_review"},
    "penalty": {"penalty", "penalty_kick", "foul"},
    "tactical_foul_yellow": {"tactical_foul_yellow", "yellow_card", "foul", "stopping_promising_attack"},
}
# "What is X?" questions go straight to the rule that defines X; ranking is only the fallback.
_TOPICS = [
    (re.compile(r"\boffside\b|fuera\s+de\s+juego", re.I), "r_law11_offside"),
    (re.compile(r"\bpenalt(?:y|ies)\b|\bspot\s*kick\b|\bpenal(?:ti)?\b", re.I), "r_law14_penalty_kick"),
    (re.compile(r"\byellow\s*card|\bbooking|\bcaution|tarjeta\s+amarilla|\bamonestaci", re.I), "r_law12_yellow_card"),
    (re.compile(r"\bred\s*card|\bsent\s*off|\bsending[\s-]*off|tarjeta\s+roja|\bexpulsi", re.I), "r_law12_red_card"),
    (re.compile(r"\bcorner|\bsaque\s+de\s+esquina|\btiro\s+de\s+esquina", re.I), "r_law17_corner_kick"),
    (re.compile(r"\bfree[\s-]*kick|tiro\s+libre", re.I), "r_law13_free_kick"),
    (re.compile(r"\bhand[\s-]*ball|\bhandling|\bmano\b|\bmanos\b", re.I), "r_law12_handball"),
    (re.compile(r"\bvar\b|video\s+(?:assistant\s+)?referee|video\s+review", re.I), "r_law5_var"),
    (re.compile(r"\badvantage|\bventaja\b", re.I), "r_law5_advantage"),
]
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
    rules: list[dict[str, Any]] = field(default_factory=list)  # for general_law: the rule(s) the agent may explain
    topic: str | None = None  # the rule id when the question names a rule topic directly (a stable cache key)


@lru_cache(maxsize=4)
def load_rule_index(project_root: str, rules_version: str) -> RuleIndex:
    path = Path(project_root) / "data" / "rules" / f"v{rules_version}" / "rules.json"
    return RuleIndex(json.loads(path.read_text(encoding="utf-8")))


def _rule_ref(rule: dict[str, Any]) -> dict[str, Any]:
    return {"ruleId": rule["rule_id"], "lawNumber": rule["law_number"], "title": rule["law_title"], "sourceUrl": rule["source_url"]}


def _names_a_topic(question: str) -> bool:
    return any(pattern.search(question) for pattern, _ in _TOPICS)


def _strong_hits(question: str, index: RuleIndex) -> list[dict[str, Any]]:
    return [rule for score, rule in index.search(question, 2) if score >= MIN_RULE_SCORE]


def _law_outcome(rules: list[dict[str, Any]], level: str, topic: str | None = None) -> AskOutcome:
    """The fixed answer (used when the model is unavailable): beginners get the plain version when one is written."""
    top = rules[0]
    refs = [_rule_ref(rule) for rule in rules]
    if level == "beginner" and top.get("plain"):
        return AskOutcome("general_law", top["plain"], refs, rules=rules[:3], topic=topic)
    return AskOutcome("general_law", f"Under the Laws of the Game: {top['summary']}", refs, rules=rules[:3], topic=topic)


def _law_answer(question: str, index: RuleIndex, level: str) -> AskOutcome | None:
    by_id = {rule["rule_id"]: rule for rule in index.rules}
    for pattern, rule_id in _TOPICS:
        if pattern.search(question) and rule_id in by_id:
            return _law_outcome([by_id[rule_id]], level, topic=rule_id)
    strong = _strong_hits(question, index)
    return _law_outcome(strong, level) if strong else None


_GREETING = re.compile(r"^\s*(hi|hello|hey|hiya|hola|yo|good\s+(?:morning|afternoon|evening)|thanks|thank\s+you|gracias)\b[\s!.?]*$", re.IGNORECASE)
_SMALLTALK = {
    "en": "Hi! Pick a recorded decision in the Decisions tab and ask me about it, or ask about the Laws of the Game, for example \"When is a red card shown?\" or \"What is offside?\"",
    "es": "¡Hola! Elige una decisión registrada en la pestaña Decisiones y pregúntame por ella, o pregunta por las Reglas de Juego, por ejemplo \"¿Cuándo se muestra una tarjeta roja?\" o \"¿Qué es el fuera de juego?\"",
}


def _text(key: str, language: str) -> str:
    return _REFUSALS[key]["es" if language.startswith("es") else "en"]


def answer_without_model(
    *, question: str, language: str, selected_event: dict | None, visible_decisions: list[dict], index: RuleIndex, level: str = "beginner",
) -> AskOutcome | None:
    """Return an outcome when the question needs no model, or None when it belongs to the selected decision."""
    if is_injection(question):
        return AskOutcome("refused", _text("refused", language))
    if _GREETING.match(question):
        return AskOutcome("smalltalk", _SMALLTALK["es" if language.startswith("es") else "en"])
    if is_future_question(question):
        return AskOutcome("out_of_scope", _text("out_of_scope", language))

    # A question about what a rule means is answered from the Laws, even when a decision is selected and shares the word.
    # The same goes for any question that names a rule topic without pointing at the moment on screen.
    if is_definition_question(question) or (_names_a_topic(question) and not refers_to_selected_moment(question)):
        definition = _law_answer(question, index, level)
        if definition is not None:
            return definition

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

    strong = _strong_hits(question, index)
    if selected_type and strong and any(set(rule.get("incident_tags", [])) & _DECISION_TAGS.get(selected_type, set()) for rule in strong):
        return None  # e.g. "Was the attacker beyond the defender?" is about the selected offside decision
    about_selected = selected_type is not None and (mentioned == selected_type or any(word in question.lower() for word in _DEICTIC_WORDS))
    if selected_type and about_selected:
        return None
    if strong:
        return _law_outcome(strong, level)
    if selected_type:
        return None
    return AskOutcome("out_of_scope", _text("out_of_scope", language))
