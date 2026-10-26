"""Deterministic fake Analyst/Referee → Storyteller workflow for local development."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from next_whistle_shared.contracts.agent import (
    AgentRequest, AgentResponse, Claim, DataFinding, DecisionRuling, EvidenceBrief,
    EvidenceFactor, EvidenceFact, Explanation, RefereeAnswer,
    Significance,
)
from next_whistle_shared.evidence.dataset import EvidenceDataset, load_evidence_dataset
from next_whistle_shared.evidence.dispatcher import EvidenceLedger, ToolDispatcher
from next_whistle_shared.validation.evidence_validator import EvidenceValidator

WORKFLOW_VERSION = "match-explainer-v1"


def run_fake_workflow(payload: AgentRequest, dataset: EvidenceDataset | None = None) -> AgentResponse:
    """Run the real evidence path with deterministic role implementations and one routed retry."""
    dataset = dataset or _load_dataset()
    context = payload.context
    assert context is not None
    tool_agent = "referee" if context.intent in {"decision", "referee_question"} else "analyst"
    ledger = EvidenceLedger()
    dispatcher = ToolDispatcher(context.for_agent(tool_agent), dataset, ledger)
    # Analyst and Referee role: retrieve only through the bound dispatcher.
    result = dispatcher.call("get_match_state", {})
    if not result.ok:
        return _fallback(payload, "Match state is unavailable.")
    if isinstance(result.data, dict):
        ledger.visible_events["__match_state__"] = {**result.data, "available_ms": context.cutoff_ms, "match_ms": result.data.get("clockMs", 0)}
    event_id = context.selected_event_id
    event_result = dispatcher.call("get_event_evidence", {"event_id": event_id, "include_build_up": True, "include_snapshots": True}) if event_id else None
    if event_result is None or not event_result.ok:
        if payload.evidence_brief is not None:
            return _legacy_response(payload)
        return _fallback(payload, "No selected cutoff-visible event is available.")
    event = event_result.data["event"]
    event_name = event["type"].replace("_", " ")
    fact = EvidenceFact(id="f1", text=f"The match record includes a {event_name} event.", evidence_ids=[event_id])
    facts = [fact]
    stats = []
    stat_result = dispatcher.call("get_match_statistics", {"metrics": ["shots", "goals"], "scope": "team"})
    if stat_result.ok:
        for row in stat_result.data["statistics"][:2]:
            stats.append({"stat_ref": row["statRef"], "value": row["value"]})
    ruling = None
    if tool_agent == "referee":
        geometry_result = dispatcher.call("get_decision_geometry", {"event_id": event_id})
        decision = event.get("decision") or {}
        rule_result = dispatcher.call("get_rule_reference", {"incident_tag": decision.get("kind", "offside")})
        if geometry_result.ok and rule_result.ok:
            geom = geometry_result.data
            laws = [r["ruleId"] for r in rule_result.data["rules"]]
            ruling = DecisionRuling(
                decision_event_id=event_id,
                decision_type=decision.get("decision_type", "offside"),
                recorded_ruling=decision.get("recorded_ruling", event.get("outcome", "not_settled")),
                law_ids=laws, requirement="The law tests the recorded decision against the relevant positions and involvement.",
                data_finding="not_settled" if geom["withinUncertainty"] else "supports",
                geometry=geom, finding_reason="The snapshot geometry is compared with the recorded ruling.",
            )
    brief = EvidenceBrief(
        brief_id="b_" + hashlib.sha256((context.request_id + event_id).encode()).hexdigest()[:16],
        intent=context.intent, event_id=event_id, facts=facts, stats=stats,
        factors=[EvidenceFactor(id="k1", text=f"The event log records {event_name}.", fact_ids=["f1"], strength="supported")],
        significance=Significance(established=False, text="The available event record does not establish wider match significance.", fact_ids=["f1"]),
        uncertainty=["This deterministic local workflow uses only the recorded synthetic match evidence."],
        ruling=ruling,
    )


    squads = _squads(dataset.matches[context.match_id].match)
    validator = EvidenceValidator(context, ledger, squads)
    brief_report = validator.validate(brief)
    retry_used = False
    if not brief_report.passed:
        # One routed retry: repeat the tool-capable agent's missing retrieval, preserving bound context.
        retry_used = True
        event_result = dispatcher.call("get_event_evidence", {"event_id": event_id, "include_build_up": True, "include_snapshots": True})
        brief_report = validator.validate(brief)
    if not brief_report.passed:
        return _fallback(payload, "Evidence validation failed; returning recorded facts only.")

    # Storyteller receives the brief and no dispatcher; its tools are allow-listed to none.
    summary = f"The match record includes a {event_name} event."
    if context.intent in {"decision", "referee_question"} and ruling:
        answer = RefereeAnswer(
            headline=f"Recorded ruling: {ruling.recorded_ruling}",
            decision=ruling.recorded_ruling,
            law={"law_ids": ruling.law_ids, "text": ruling.requirement},
            data_shows=DataFinding(finding=ruling.data_finding, text=ruling.finding_reason),
            uncertainty="The geometry is limited to the recorded snapshot.",
            claims=[Claim(id="c1", kind="fact", text=f"Recorded ruling: {ruling.recorded_ruling}.", evidence_ids=[event_id])],
        )
        raw_answer = answer.model_dump(mode="python")
        response_answer = answer.model_dump_json(by_alias=True)
        explanation = None
        referee_answer = answer
    else:
        explanation = Explanation(
            headline=f"Recorded {event_name}", what_happened=summary,
            factors=[EvidenceFactor(id="k1", text=f"The event log records {event_name}.", fact_ids=["f1"], strength="supported")],
            why_it_matters="The supplied record confirms the event, while broader match impact is not established.",
            significance_established=False, uncertainty="Broader match impact is not established by this event alone.",
            claims=[Claim(id="c1", kind="fact", text=summary, evidence_ids=[event_id])],
        )
        raw_answer = explanation.model_dump(mode="python")
        response_answer = f"{explanation.headline}. {explanation.what_happened} {explanation.why_it_matters}"
        referee_answer = None
    answer_report = validator.validate(raw_answer)
    if not answer_report.passed and not retry_used:
        retry_used = True  # routed to Storyteller; no tools are available on this retry
        answer_report = validator.validate(raw_answer)
    if not answer_report.passed:
        return _fallback(payload, "Answer validation failed; returning recorded facts only.")
    return AgentResponse(
        request_id=payload.request_id, status="ok", source="fake", workflow_version=WORKFLOW_VERSION,
        answer=response_answer, evidence_ids=sorted({event_id}), evidence_brief=brief,
        explanation=explanation, referee_answer=referee_answer,
    )



def fake_answer(payload: AgentRequest) -> str:
    """Compatibility helper for the original flat Core scaffold."""
    if payload.evidence_brief is None:
        return "The supplied evidence is insufficient to explain this event."
    brief = payload.evidence_brief
    return f"{brief.headline}. {brief.summary} {' '.join(brief.facts)}"


def _legacy_response(payload: AgentRequest) -> AgentResponse:
    brief = payload.evidence_brief
    answer = f"{brief.headline}. {brief.summary} {' '.join(brief.facts)}"
    return AgentResponse(request_id=payload.request_id, status="ok", source="fake", workflow_version=WORKFLOW_VERSION, answer=answer, evidence_ids=brief.evidence_ids)


def _fallback(payload: AgentRequest, message: str) -> AgentResponse:
    brief = payload.evidence_brief
    answer = (f"{brief.headline}. {brief.summary} {' '.join(brief.facts)}" if brief else message)
    return AgentResponse(request_id=payload.request_id, status="fallback", source="template", workflow_version=WORKFLOW_VERSION, answer=answer, evidence_ids=brief.evidence_ids if brief else [])


def _load_dataset() -> EvidenceDataset:
    configured = os.getenv("AGENT_DATASET_PATH")
    root = Path(configured) if configured else Path(__file__).resolve().parents[5] / "data" / "datasets" / "v1.3.0"
    return load_evidence_dataset(root)


def _squads(match: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["player_id"]: p for side in (match["home"], match["away"]) for p in side["squad"]}
