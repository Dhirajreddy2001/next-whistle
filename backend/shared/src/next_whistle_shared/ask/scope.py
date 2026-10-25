"""Deterministic pre-checks for free-text Ask the Ref questions.

Question text is data. These checks run before any model call, so a refused question costs no tokens.
"""

from __future__ import annotations

import re

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACES = re.compile(r"\s+")
_INJECTION = re.compile(
    r"ignore\s+(all\s+|any\s+)?(previous|prior|above|earlier)|disregard\s+(the\s+|all\s+|your\s+)?(instructions|rules|above)"
    r"|system\s+prompt|developer\s+(message|prompt)|reveal\s+(your|the)\s+(prompt|instructions)|you\s+are\s+now"
    r"|jailbreak|pretend\s+(to\s+be|you)|act\s+as\s+(a|an|the)?\s*\w+|</?\s*(system|assistant|developer|tool)\s*>|\[\s*(system|inst)\s*\]"
    r"|^\s*(system|assistant|developer)\s*:",
    re.IGNORECASE | re.MULTILINE,
)
_FUTURE = re.compile(
    r"\b(will|going\s+to|gonna|predict(ion)?|who\s+wins?|who\s+will|next\s+goal|final\s+score|end\s+up|odds|bet(ting)?|forecast|later\s+in\s+the\s+match)\b",
    re.IGNORECASE,
)

# "What is offside?", "I don't know what a penalty means": a question about the rule itself, not about this decision.
_DEFINITION = re.compile(
    r"\b(what\s+is|what's|whats|what\s+are|what\s+does\s+.{1,40}\s+mean|what\s+do\s+you\s+mean|meaning\s+of|define|definition\s+of"
    r"|(?:don'?t|do\s+not)\s+(?:know|understand)\s+what|explain\s+(?:what|the\s+rule)|how\s+does\s+.{1,30}\s+work)\b",
    re.IGNORECASE,
)
# Words that tie a question to the selected moment.
_DEICTIC = re.compile(r"\b(this|that|these|those|here|the\s+(?:flag|decision|call|ruling|goal)|why\s+(?:was|did|is))\b", re.IGNORECASE)

# Phrases that point at a recorded decision type in the match data.
DECISION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "offside": ("offside", "onside", "flag", "linesman", "assistant referee"),
    "penalty": ("penalty", "spot kick", "spot-kick"),
    "tactical_foul_yellow": ("yellow", "caution", "booked", "booking", "tactical foul"),
}


def sanitize_question(text: str, limit: int = 300) -> str:
    """Strip control characters, collapse whitespace and enforce the length limit."""
    return _SPACES.sub(" ", _CONTROL.sub(" ", text)).strip()[:limit]


def is_injection(text: str) -> bool:
    return bool(_INJECTION.search(text))


def is_future_question(text: str) -> bool:
    return bool(_FUTURE.search(text))


def is_definition_question(text: str) -> bool:
    """True when the viewer is asking what a rule or term means, with no reference to the selected moment."""
    return bool(_DEFINITION.search(text)) and not _DEICTIC.search(text)


def refers_to_selected_moment(text: str) -> bool:
    """True when the question points at the moment on screen ("this", "that", "why was", "the flag")."""
    return bool(_DEICTIC.search(text))


def mentioned_decision_type(text: str) -> str | None:
    lowered = text.lower()
    for decision_type, words in DECISION_KEYWORDS.items():
        if any(re.search(rf"\b{re.escape(word)}", lowered) for word in words):
            return decision_type
    return None
