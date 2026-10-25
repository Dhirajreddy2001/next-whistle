"""Deterministic V1–V17 checks for briefs and user-facing agent output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from next_whistle_shared.contracts.agent import (
    BoundContext, CriticVerdict, EvidenceBrief, Explanation, LawExplanation, RefereeAnswer,
)
from next_whistle_shared.evidence.dispatcher import EvidenceLedger


@dataclass(frozen=True)
class ValidationIssue:
    check_id: str
    severity: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]

    @property
    def passed(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


class EvidenceValidator:
    VERSION = "1.0.0"
    def __init__(self, context: BoundContext, ledger: EvidenceLedger, squads: dict[str, dict] | None = None):
        self.context, self.ledger, self.squads = context, ledger, squads or {}

    def validate(self, value: Any) -> ValidationReport:
        issues: list[ValidationIssue] = []
        raw = value.model_dump(mode="python", by_alias=False) if hasattr(value, "model_dump") else value
        is_brief = isinstance(value, EvidenceBrief) or (isinstance(raw, dict) and ("brief_id" in raw or "briefId" in raw))
        if isinstance(value, EvidenceBrief):
            schema_type = EvidenceBrief
        elif isinstance(value, RefereeAnswer): schema_type = RefereeAnswer
        elif isinstance(value, LawExplanation): schema_type = LawExplanation
        elif isinstance(value, Explanation): schema_type = Explanation
        elif isinstance(value, CriticVerdict): schema_type = CriticVerdict
        else:
            schema_type = RefereeAnswer if isinstance(raw, dict) and ("data_shows" in raw or "dataShows" in raw) else Explanation
        try:
            if not isinstance(value, schema_type): schema_type.model_validate(value)
        except (ValidationError, TypeError, ValueError) as exc:
            issues.append(ValidationIssue("V1", "error", f"Output schema invalid: {str(exc)[:180]}"))
            return ValidationReport(tuple(issues))

        facts = raw.get("facts", []) if is_brief else []
        fact_ids = {f["id"] for f in facts}
        stats = raw.get("stats", []) if is_brief else []
        for ev_id in _evidence_ids(raw):
            if ev_id not in self.ledger.evidence_ids: issues.append(_issue("V2", f"Evidence ID {ev_id} is absent from the tool ledger."))
        stat_refs = _stat_refs(raw)
        for ref in stat_refs:
            if ref not in self.ledger.stat_values: issues.append(_issue("V3", f"Stat reference {ref} is absent from the tool ledger."))
        for claim in raw.get("claims", []):
            if claim.get("kind") == "number" and claim.get("stat_ref"):
                expected = self.ledger.stat_values.get(claim["stat_ref"])
                if expected is None or not _close(claim.get("value"), expected, .05): issues.append(_issue("V4", f"Claim {claim.get('id')} does not match {claim['stat_ref']}."))
            if claim.get("kind") == "number" and claim.get("geometry_ref"):
                geometry = self.ledger.geometries.get(claim["geometry_ref"], {})
                target = geometry.get("marginM", geometry.get("distanceM"))
                if target is None or not _close(claim.get("value"), target, .01): issues.append(_issue("V17", f"Claim {claim.get('id')} does not match recorded geometry."))
        for stat in stats:
            expected = self.ledger.stat_values.get(stat.get("stat_ref"))
            if expected is None or stat.get("value") != expected:
                issues.append(_issue("V4", f"Brief statistic {stat.get('stat_ref')} does not match the ledger."))

        prose = _prose(raw)
        numbers = [c for c in raw.get("claims", []) if c.get("kind") == "number"]
        claimed = {str(c.get("value")) for c in numbers}
        scoreless = re.sub(r"\b\d+\s*[–-]\s*\d+\b", " ", prose)  # scores are checked separately by V7
        # V5 protects the viewer-facing answer. A brief's facts carry numbers straight from tool results, so it is exempt.
        for token in ([] if is_brief else re.findall(r"(?<![\w.])\d+(?:\.\d+)?", scoreless)):
            if token in self.ledger.allowed_numbers:
                continue  # a number that appears in the rule text being explained
            if re.search(rf"\bLaws?\s+{re.escape(token)}\b", prose):
                continue  # a Law number such as "Law 11" is a reference, not a statistic
            if re.search(rf"\b{re.escape(token)}(?:st|nd|rd|th)\b|\b{re.escape(token)}\s*(?:minutes?|mins?)\b|\b(?:minutes?|minutos?)\s*{re.escape(token)}\b|\b{re.escape(token)}\s*minutos?\b", prose, re.I):
                continue  # a minute like "the 61st minute"; minutes are checked against the match clock by V6
            if token not in claimed and not re.search(rf"\b{re.escape(token)}\s*[-–']", prose) and not re.search(rf"\b{re.escape(token)}\s*[-–]\s*\d+", prose):
                issues.append(_issue("V5", f"The number {token} appears in the text without a number claim. Delete it, or describe it in words without digits.")); break
        if self.context.cutoff_ms < (self.ledger.visible_events and max(e.get("match_ms", 0) for e in self.ledger.visible_events.values()) or 0):
            issues.append(_issue("V6", "Visible evidence exceeds the bound cutoff."))
        if any(e.get("available_ms", e.get("availableMs", 0)) > self.context.cutoff_ms for e in self.ledger.visible_events.values()):
            issues.append(_issue("V6", "The evidence ledger contains an item beyond the bound availability cutoff."))
        for minute in re.findall(r"\b(\d{1,3})(?:\+\d+)?\s*['’]", prose):
            if (int(minute) - 1) * 60_000 > self.context.cutoff_ms:  # the Nth minute is the one in progress
                issues.append(_issue("V6", f"Minute {minute} is later than the bound match clock."))
        score = re.search(r"\b(\d+)\s*[–-]\s*(\d+)\b", prose)
        if score:
            state = self.ledger.visible_events.get("__match_state__", {}).get("score")
            # "1–0" may be written from the leader's side, so the two numbers are compared, not their order
            if state and sorted((int(score[1]), int(score[2]))) != sorted((state.get("home"), state.get("away"))): issues.append(_issue("V7", "Prose score differs from match state."))
        for rule in _rule_ids(raw):
            if rule not in self.ledger.rules: issues.append(_issue("V8", f"Rule {rule} is absent from the tool ledger."))
        focus = raw.get("focus_player")
        if focus and focus.get("involved"):
            player = focus.get("player_id")
            if not any(player in (e.get("player_id"), e.get("target_player_id")) for e in self.ledger.visible_events.values()): issues.append(_issue("V9", "Focus player is not involved in cited visible evidence."))
        allowed_names = {p.get("name") for p in self.squads.values()}
        body_prose = _prose({k: v for k, v in raw.items() if k != "headline"}, sep=".\n") if isinstance(raw, dict) else prose  # each field starts a new sentence  # title-case headlines are not names
        unknown = _unknown_names(body_prose, allowed_names)
        if unknown:
            issues.append(_issue("V10", f"Unrecognized name '{unknown}'. Refer to players and clubs only by the exact names in the brief."))
        if not is_brief:
            leaked = _internal_identifier(prose)
            if leaked:
                issues.append(_issue("V19", f"'{leaked}' is an internal identifier or field name. Describe it in plain words instead."))
        if isinstance(value, Explanation) and not value.why_it_matters and (value.significance_established or not value.uncertainty): issues.append(_issue("V11", "Significance needs an explanation or explicit uncertainty."))
        if self.context.language != "en": issues.append(_issue("V12", "Language detection requires a configured language detector.", "warning"))
        limit = {"beginner": 120, "intermediate": 180, "pro": 250}[self.context.level]
        if len(prose.split()) > limit: issues.append(_issue("V13", f"Output exceeds the {limit}-word level limit.", "warning"))
        max_numbers = {"beginner": 2, "intermediate": 4, "pro": 6}[self.context.level]
        if not is_brief and len(numbers) > max_numbers: issues.append(_issue("V18", f"{len(numbers)} number claims exceed the {max_numbers}-number limit for a {self.context.level} viewer."))
        if re.search(r"\b(bet|betting|odds|gambling)\b|https?://|<[^>]+>", prose, re.I): issues.append(_issue("V14", "Forbidden betting, URL, or HTML content."))
        if is_brief:
            significance = raw.get("significance", {})
            if not significance.get("text") and (significance.get("established") or not raw.get("uncertainty")):
                issues.append(_issue("V11", "Brief significance needs support or explicit uncertainty."))
            for fact in facts:
                returned = self.ledger.evidence_ids | self.ledger.known_ids | self.ledger.rules | set(self.ledger.stat_values) | set(self.ledger.geometries)
                unseen = [e for e in fact.get("evidence_ids", []) if e not in returned]
                if not fact.get("evidence_ids") or unseen:
                    issues.append(_issue("V15", f"Brief fact {fact.get('id')} cites evidence that no tool returned ({', '.join(unseen) or 'none given'}). Cite only event IDs that appear in tool results, or call get_event_evidence for them first."))
            for stat in stats:
                if stat.get("stat_ref") not in self.ledger.stat_values: issues.append(_issue("V15", f"Brief stat {stat.get('stat_ref')} is not in ledger."))
            for player_id, event_ids in raw.get("involvement", {}).items():
                if not event_ids or any(event_id not in self.ledger.evidence_ids for event_id in event_ids):
                    issues.append(_issue("V15", f"Involvement for {player_id} lacks ledger evidence."))
            for factor in raw.get("factors", []):
                if any(fid not in fact_ids for fid in factor.get("fact_ids", [])): issues.append(_issue("V15", "Factor refers to an unknown brief fact."))
            ruling = raw.get("ruling")
            if ruling and ruling.get("decision_event_id") in self.ledger.visible_events:
                recorded = self.ledger.visible_events[ruling["decision_event_id"]].get("decision", {}).get("recorded_ruling")
                if recorded and ruling.get("recorded_ruling") != recorded: issues.append(_issue("V16", "Brief ruling differs from the recorded ruling."))
        elif schema_type is RefereeAnswer:
            selected = self.ledger.visible_events.get(self.context.selected_event_id or "", {})
            recorded = (selected.get("decision") or {}).get("recorded_ruling")
            decision = raw.get("decision")
            if recorded and decision != recorded:
                issues.append(_issue("V16", "Referee answer differs from the recorded ruling."))
        return ValidationReport(tuple(issues))


# Capitalised words that are not names: the Laws, the governing body and common football terms.
_NOT_NAMES = {"Law", "Laws", "Game", "IFAB", "VAR", "FC", "AFC", "Football", "Referee", "English", "Spanish", "French", "German",
              # words from the titles of the Laws, which are capitalised when a Law is named
              "Misconduct", "Fouls", "Penalty", "Kick", "Kicks", "Offside", "Advantage", "Corner", "Throw", "Goal", "Players", "Free"}
_CAPITALISED = re.compile(r"(?<![\w'’])([A-Z][a-z]+)(?![\w])")
_INTERNAL = re.compile(r"\b[a-z]+[A-Z][A-Za-z]*\b|\b[epstr]_[a-z0-9_]{2,}\b|\b(?:geometryRef|statRef)\b")


def _unknown_names(prose: str, allowed_names: set) -> str | None:
    """A capitalised word in the middle of a sentence that is part of no known name. Sentence-initial words are skipped:
    they are capitalised only because they start a sentence ("Under Law 11", "Why Nia Cole")."""
    parts = {part for name in allowed_names if name for part in name.split()}
    if not parts:
        return None
    for match in _CAPITALISED.finditer(prose):
        word = match.group(1)
        before = prose[: match.start()].rstrip()
        if not before or before[-1] in ".!?:;\n\"“(" or word in parts or word in _NOT_NAMES:
            continue
        return word
    return None


def _internal_identifier(prose: str) -> str | None:
    match = _INTERNAL.search(prose)
    return match.group(0) if match else None


def _issue(check, message, severity="error"): return ValidationIssue(check, severity, message)
def _close(a, b, tolerance): return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a-b) <= tolerance
def _walk(x):
    if isinstance(x, dict):
        yield x
        for v in x.values(): yield from _walk(v)
    elif isinstance(x, list):
        for v in x: yield from _walk(v)
def _evidence_ids(x): return {e for item in _walk(x) for k in ("evidence_ids", "event_ids") for e in item.get(k, []) if isinstance(e, str) and e.startswith("e_")}
def _stat_refs(x): return {item["stat_ref"] for item in _walk(x) if item.get("stat_ref")}
def _rule_ids(x): return {r for item in _walk(x) for r in item.get("law_ids", []) if isinstance(r, str)} | {item["rule"]["rule_id"] for item in _walk(x) if isinstance(item.get("rule"), dict) and item["rule"].get("rule_id")}
def _prose(x, sep=" "): return sep.join(str(v) for item in _walk(x) for k, v in item.items() if isinstance(v, str) and k not in {"id", "player_id", "event_id", "stat_ref", "geometry_ref", "rule_id", "brief_id"})
