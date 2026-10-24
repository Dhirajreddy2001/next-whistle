"""Cutoff-bound evidence preparation and factual generation fallbacks."""

import json
from pathlib import Path

from fastapi import HTTPException

from next_whistle_core.store.datasets import MatchData
from next_whistle_shared.contracts.agent import AgentEvidenceBrief, AgentRequest, AgentResponse, AgentViewer, QuestionTurn


def project_root() -> Path:
    return Path(__file__).resolve().parents[6]


def build_agent_request(
    *, request_id: str, intent: str, match_id: str, dataset_version: str,
    data: MatchData, cutoff_ms: int, event_id: str | None = None,
    question_id: str | None = None, question_text: str | None = None,
    language: str = "en", history: list[dict] | None = None,
) -> AgentRequest:
    visible = [event for event in data.events if event["available_ms"] <= cutoff_ms]
    if event_id:
        selected = next((event for event in visible if event["event_id"] == event_id), None)
        if selected is None:
            raise HTTPException(404, "Event not visible at cutoff")
        events = [event for event in visible if event["available_ms"] >= max(0, cutoff_ms - 50 * 60_000)]
        if selected not in events:
            events.append(selected)
        events.sort(key=lambda event: (event["available_ms"], event["seq"]))
    else:
        events = [event for event in visible if event["available_ms"] >= max(0, cutoff_ms - 50 * 60_000)]
    if not events:
        raise HTTPException(422, "No match evidence is available at this cutoff")

    selected = next((event for event in events if event["event_id"] == event_id), None) if event_id else None
    focus = selected or events[-1]
    evidence_ids = [event["event_id"] for event in events[-10:]]
    if focus["event_id"] not in evidence_ids:
        evidence_ids = [focus["event_id"], *evidence_ids[-9:]]
    summary = (
        f"Recorded event type: {focus['type']}; outcome: {focus.get('outcome') or 'not recorded'}; "
        f"match minute: {focus['match_ms'] // 60_000}. Only events available by {cutoff_ms} ms are included."
    )
    facts = [
        f"Visible event count in the 50-minute context: {len(events)}.",
        f"Selected evidence event: {focus['event_id']} ({focus['type']}) at {focus['match_ms']} ms.",
    ]
    if focus.get("decision"):
        decision = focus["decision"]
        facts.append(f"Recorded ruling: {decision['recorded_ruling']} ({decision['decision_type']}).")
        snapshot = next((row for row in data.snapshots
                         if row["snapshot_id"] == decision.get("geometry_snapshot_id")
                         and row["available_ms"] <= cutoff_ms), None)
        if snapshot:
            facts.append(
                f"Supplied geometry snapshot {snapshot['snapshot_id']} at {snapshot['available_ms']} ms; "
                f"ball position ({snapshot['ball']['x']:.2f}, {snapshot['ball']['y']:.2f}) metres."
            )
            for player_id in (focus.get("player_id"), focus.get("target_player_id")):
                player_position = next((row for row in snapshot["players"] if row["player_id"] == player_id), None)
                if player_position:
                    facts.append(
                        f"Player {player_id} position ({player_position['x']:.2f}, {player_position['y']:.2f}) metres."
                    )
    return AgentRequest(
        request_id=request_id, intent=intent, match_id=match_id,
        dataset_version=dataset_version, cutoff_ms=cutoff_ms,
        evidence_cutoff_ms=cutoff_ms, event_id=event_id,
        question_id=question_id, question_text=question_text,
        history=[QuestionTurn(question=turn["question"], answer_headline=turn.get("answer_headline", "")) for turn in (history or [])],
        viewer=AgentViewer(language=language),
        evidence_brief=AgentEvidenceBrief(
            headline=f"{focus['type'].replace('_', ' ').title()} at {focus['match_ms'] // 60_000}'",
            summary=summary, facts=facts, evidence_ids=evidence_ids,
        ),
    )


def fallback_response(payload: AgentRequest, *, answer: str) -> AgentResponse:
    return AgentResponse(
        request_id=payload.request_id, status="fallback", source="template",
        workflow_version="template-v1", answer=answer,
        evidence_ids=payload.evidence_brief.evidence_ids,
    )


def supported_question(data: MatchData, event_id: str, question_id: str, language: str) -> str:
    event = next((row for row in data.events if row["event_id"] == event_id), None)
    if event is None or not event.get("decision"):
        raise HTTPException(422, "Ask the Ref is available for recorded decisions")
    questions_path = project_root() / "data/rules/questions.yaml"
    rows = json.loads(questions_path.read_text(encoding="utf-8"))
    question = next((row for row in rows if row["question_id"] == question_id), None)
    if question is None or question["decision_type"] != event["decision"]["decision_type"]:
        raise HTTPException(422, "Question is not supported for this decision")
    return question["text"].get(language) or question["text"]["en"]


def referee_fallback(data: MatchData, event: dict, language: str) -> tuple[str, list[dict]]:
    decision = event["decision"]
    root = project_root()
    rules = json.loads((root / "data/rules/v1.2.0/rules.json").read_text(encoding="utf-8"))
    tags = {decision["decision_type"], *event.get("qualifiers", [])}
    rule = next((row for row in rules if tags.intersection(row.get("incident_tags", []))), None)
    recorded = decision["recorded_ruling"].replace("_", " ")
    if language.startswith("es"):
        answer = f"La decisión registrada fue: {recorded}. La geometría y los datos disponibles se limitan a la evidencia sintética del partido."
    else:
        answer = f"The recorded decision was {recorded}. The available geometry and details are limited to this synthetic match's supplied evidence."
    refs = []
    if rule:
        refs.append({"ruleId": rule["rule_id"], "lawNumber": rule["law_number"], "title": rule["law_title"], "sourceUrl": rule["source_url"]})
        answer += f" The relevant principle is summarized in IFAB Law {rule['law_number']}: {rule['summary']}"
    return answer, refs
