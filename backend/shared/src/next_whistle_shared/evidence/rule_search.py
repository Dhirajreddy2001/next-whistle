"""Deterministic keyword retrieval (BM25) over the project-authored Laws summaries."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Iterable

K1, B = 1.5, 0.75
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have how i if in is it its of on or "
    "that the their then there they this to was what when where which who why will with".split()
)
_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    tokens = []
    for word in _TOKEN.findall(text.lower()):
        if word in STOPWORDS:
            continue
        for suffix, replacement in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
            if len(word) > len(suffix) + 2 and word.endswith(suffix):
                word = word[: -len(suffix)] + replacement
                break
        tokens.append(word)
    return tokens


class RuleIndex:
    def __init__(self, rules: Iterable[dict[str, Any]]):
        self.rules = tuple(rules)
        self._documents: list[Counter[str]] = []
        for rule in self.rules:
            # Titles, sections and keywords are weighted by repeating them.
            text = " ".join([
                rule["summary"], rule["law_title"], rule["law_title"], f"law {rule['law_number']}",
                rule.get("keywords", ""), rule.get("keywords", ""), " ".join(rule.get("incident_tags", [])).replace("_", " "),
            ])
            self._documents.append(Counter(tokenize(text)))
        self._lengths = [sum(doc.values()) for doc in self._documents]
        self._average = (sum(self._lengths) / len(self._lengths)) if self._lengths else 1.0
        frequency: Counter[str] = Counter()
        for doc in self._documents:
            frequency.update(doc.keys())
        count = len(self._documents)
        self._idf = {term: math.log(1 + (count - df + 0.5) / (df + 0.5)) for term, df in frequency.items()}

    def search(self, query: str, max_results: int = 3) -> list[tuple[float, dict[str, Any]]]:
        terms = tokenize(query)
        scored = []
        for rule, doc, length in zip(self.rules, self._documents, self._lengths):
            score = 0.0
            for term in terms:
                tf = doc.get(term, 0)
                if tf:
                    score += self._idf.get(term, 0.0) * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / self._average))
            if score > 0:
                scored.append((round(score, 4), rule))
        scored.sort(key=lambda row: (-row[0], row[1]["rule_id"]))
        return scored[:max_results]
