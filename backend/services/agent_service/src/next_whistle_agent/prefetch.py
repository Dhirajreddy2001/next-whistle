"""Standard evidence for the buttons (explain, recap, suggested questions), fetched in code.

The questions these requests ask never change: what happened, who was involved, the score, the headline
statistics, and the Law and measured geometry when a decision is selected. Fetching that set directly saves the
model six or more tool round trips. Free-text questions still let the model choose its own tools.
"""

from __future__ import annotations

import json
from typing import Any

from next_whistle_shared.contracts.agent import AgentRequest, BoundContext
from next_whistle_shared.evidence.dispatcher import ToolDispatcher

RECAP_METRICS = ["goals", "shots", "shots_on_target", "possession_pct"]
MOMENT_METRICS = ["goals", "shots"]  # a single moment needs little; fewer figures keep the answer within a viewer's number limit
SUPPORTED_DECISIONS = {"offside", "tactical_foul_yellow", "penalty"}
# What the Laws call each kind of recorded event, for the rule lookup.
RULE_TAGS = {"card_yellow": "yellow_card", "card_red": "red_card", "offside": "offside", "foul": "foul", "substitution": "substitution", "goal_disallowed": "goal_review"}


def is_free_text(payload: AgentRequest) -> bool:
    return bool(payload.question_text) and payload.question_id is None


def prefetch(dispatcher: ToolDispatcher, context: BoundContext) -> list[dict[str, Any]]:
    """Run the standard tools through the bound dispatcher; the result has the shape of a model's tool transcript."""
    transcript: list[dict[str, Any]] = []

    def call(name: str, arguments: dict[str, Any]):
        if name not in dispatcher.context.allowed_tools:  # a role gets only the tools it is allowed to use
            return None
        result = dispatcher.call(name, arguments)
        transcript.append({"tool": name, "arguments": arguments, "result": json.loads(result.model_dump_json(by_alias=True))})
        return result

    state = call("get_match_state", {})
    event_id = context.selected_event_id or (state.data.get("lastEventId") if state and state.ok else None)
    if event_id:
        evidence = call("get_event_evidence", {"event_id": event_id, "include_build_up": True, "include_snapshots": False})
        if evidence and evidence.ok:
            event = evidence.data["event"]
            decision = event.get("decision")
            if decision and dispatcher.context.agent == "referee" and decision["decision_type"] in SUPPORTED_DECISIONS:
                call("get_decision_geometry", {"event_id": event_id})
                call("get_rule_reference", {"incident_tag": decision["kind"] if decision["kind"] in {"offside", "penalty"} else "tactical_foul_yellow"})
            elif event["type"] in RULE_TAGS:
                call("get_rule_reference", {"incident_tag": RULE_TAGS[event["type"]]})
            if event.get("playerId"):
                call("get_player_summary", {"player_id": event["playerId"]})
    call("get_match_statistics", {"metrics": MOMENT_METRICS if context.selected_event_id else RECAP_METRICS, "scope": "team"})
    return transcript
