"""Rebuild the evidence ledger for a brief that was cached earlier, with no model call and no tool budget.

A cached brief is only reused after every item in it is checked against the dataset again: each event it cites must
exist and have been visible at the cutoff the brief was computed for.
"""

from __future__ import annotations

from next_whistle_shared.contracts.agent import EvidenceBrief
from .dataset import EvidenceDataset
from .dispatcher import EvidenceLedger


def cited_event_ids(brief: EvidenceBrief) -> set[str]:
    ids = {event_id for fact in brief.facts for event_id in fact.evidence_ids if event_id.startswith("e_")}
    ids.update(event_id for events in brief.involvement.values() for event_id in events)
    if brief.event_id:
        ids.add(brief.event_id)
    if brief.ruling is not None:
        ids.add(brief.ruling.decision_event_id)
    return ids


def ledger_from_brief(brief: EvidenceBrief, dataset: EvidenceDataset, match_id: str, cutoff_ms: int) -> EvidenceLedger | None:
    """The ledger that retrieval would have produced, or None if the brief cannot be trusted at this cutoff."""
    match = dataset.matches.get(match_id)
    if match is None:
        return None
    by_id = {event["event_id"]: event for event in match.events}
    ledger = EvidenceLedger()
    rule_ids = {rule["rule_id"] for rule in dataset.rules}
    snapshot_ids = {snapshot["snapshot_id"] for snapshot in match.snapshots if snapshot["available_ms"] <= cutoff_ms}
    for fact in brief.facts:
        for other in fact.evidence_ids:
            if other.startswith("r_") and other in rule_ids:
                ledger.known_ids.add(other)
            elif other.startswith("s_") and other in snapshot_ids:
                ledger.known_ids.add(other)
            elif not other.startswith("e_"):
                return None  # cites something that is neither an event, a rule nor a visible snapshot
    for event_id in cited_event_ids(brief):
        event = by_id.get(event_id)
        if event is None or event["available_ms"] > cutoff_ms:
            return None
        ledger.evidence_ids.add(event_id)
        ledger.visible_events[event_id] = {**event, "eventId": event_id, "availableMs": event["available_ms"]}
    for stat in brief.stats:
        ledger.stat_values[stat.stat_ref] = stat.value
    ruling = brief.ruling
    if ruling is not None:
        ledger.rules.update(ruling.law_ids)
        geometry = ruling.geometry or {}
        if geometry.get("geometryRef"):
            ledger.geometries[geometry["geometryRef"]] = geometry
    return ledger
