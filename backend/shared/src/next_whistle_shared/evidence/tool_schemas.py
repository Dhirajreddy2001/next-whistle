"""Strict argument models and JSON Schema declarations for evidence tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

INCIDENT_TAGS = Literal[
    "substitution", "red_card", "second_yellow", "advantage", "var_review", "goal_review",
    "added_time", "half_time", "full_time", "kickoff", "dropped_ball", "ball_out_of_play",
    "goal", "penalty_shootout", "offside", "foul", "reckless_foul", "tactical_foul_yellow",
    "stopping_promising_attack", "yellow_card", "denying_goal_scoring_opportunity",
    "serious_foul_play", "handball", "free_kick", "free_kick_goal", "penalty", "penalty_kick",
    "penalty_missed", "penalty_saved", "throw_in", "goal_kick", "shot_miss", "corner",
]
METRICS = Literal[
    "passes_attempted", "passes_completed", "pass_completion_pct", "avg_pass_distance_m",
    "long_passes", "shots", "shots_on_target", "goals", "final_third_entries",
    "touches", "recoveries", "pressures", "possession_pct",
]


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmptyArgs(ToolArgs):
    pass


class GetEventEvidenceArgs(ToolArgs):
    event_id: str = Field(pattern=r"^e_[a-z0-9_]+$")
    include_build_up: bool = True
    include_snapshots: bool = False  # full player positions are large; decision geometry has its own tool


class GetMatchStatisticsArgs(ToolArgs):
    metrics: list[METRICS] = Field(min_length=1, max_length=6)
    scope: Literal["team", "window"] = "team"
    team_id: str | None = Field(default=None, pattern=r"^t_[a-z0-9_]+$")


class GetPlayerSummaryArgs(ToolArgs):
    player_id: str = Field(pattern=r"^p_[a-z0-9_]+$")


class FindSimilarEventsArgs(ToolArgs):
    event_id: str = Field(pattern=r"^e_[a-z0-9_]+$")
    max_results: int = Field(default=3, ge=1, le=3)


class GetRuleReferenceArgs(ToolArgs):
    incident_tag: INCIDENT_TAGS


class SearchRulesArgs(ToolArgs):
    query: str = Field(min_length=3, max_length=200)
    max_results: int = Field(default=3, ge=1, le=5)


class GetDecisionGeometryArgs(ToolArgs):
    event_id: str = Field(pattern=r"^e_[a-z0-9_]+$")


TOOL_ARGUMENT_MODELS: dict[str, type[ToolArgs]] = {
    "get_match_state": EmptyArgs,
    "get_event_evidence": GetEventEvidenceArgs,
    "get_match_statistics": GetMatchStatisticsArgs,
    "get_player_summary": GetPlayerSummaryArgs,
    "find_similar_events": FindSimilarEventsArgs,
    "get_rule_reference": GetRuleReferenceArgs,
    "search_rules": SearchRulesArgs,
    "get_decision_geometry": GetDecisionGeometryArgs,
}


def tool_json_schemas() -> list[dict]:
    """OpenAI/Responses-style strict function declarations; context is never an argument."""
    descriptions = {
        "get_match_state": "Current match state at the bound viewer cutoff.",
        "get_event_evidence": "A cutoff-visible event, its possession build-up, and available snapshots.",
        "get_match_statistics": "Deterministic match or window statistics with statRef identifiers.",
        "get_player_summary": "Player metrics and recent cutoff-visible involvement.",
        "find_similar_events": "Earlier events with deterministic similarity scores.",
        "get_rule_reference": "Pinned rule records for one supported incident tag.",
        "search_rules": "Keyword search over the pinned Laws of the Game summaries for a general rules question.",
        "get_decision_geometry": "Decision geometry computed only from a visible recorded snapshot.",
    }
    declarations = []
    for name, model in TOOL_ARGUMENT_MODELS.items():
        schema = model.model_json_schema()
        schema["additionalProperties"] = False
        schema["required"] = list(schema.get("properties", {}))
        declarations.append({
            "type": "function", "name": name, "description": descriptions[name],
            "parameters": schema, "strict": True,
        })
    return declarations
