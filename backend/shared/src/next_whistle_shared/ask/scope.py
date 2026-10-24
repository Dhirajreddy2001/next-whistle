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


def mentioned_decision_type(text: str) -> str | None:
    lowered = text.lower()
    for decision_type, words in DECISION_KEYWORDS.items():
        if any(re.search(rf"\b{re.escape(word)}", lowered) for word in words):
            return decision_type
    return None
