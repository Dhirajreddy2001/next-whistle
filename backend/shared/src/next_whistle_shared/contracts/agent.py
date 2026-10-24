"""Versioned Core/Agent contracts and evidence-bound structured outputs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

Intent = Literal["explain", "compare", "decision", "recap", "referee_question"]
AgentRole = Literal["analyst", "referee", "storyteller", "critic"]


class ContractModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class AgentViewer(ContractModel):
    level: Literal["beginner", "intermediate", "pro"] = "beginner"
    language: str = Field(default="en", min_length=2, max_length=12)
    club_id: str | None = None
    focus_player_id: str | None = None


ANALYST_TOOLS = frozenset({
    "get_match_state", "get_event_evidence", "get_match_statistics", "get_player_summary",
})
REFEREE_TOOLS = frozenset({
    "get_match_state", "get_event_evidence", "get_decision_geometry", "get_rule_reference", "search_rules",
})


class BoundContext(ContractModel):
    """Immutable request authority. Tool arguments never supply match or time."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid", frozen=True,
    )

    request_id: str = Field(pattern=r"^req_[a-zA-Z0-9_-]{1,80}$")
    match_id: str = Field(pattern=r"^m_[a-z0-9_]+$")
    dataset_version: str = Field(min_length=1, max_length=32)
    rules_version: str = Field(default="1.2.0", min_length=1, max_length=32)
    cutoff_ms: int = Field(ge=0)
    evidence_cutoff_ms: int = Field(ge=0)
    level: Literal["beginner", "intermediate", "pro"] = "beginner"
    language: str = Field(default="en", min_length=2, max_length=12)
    perspective_team_id: str | None = None
    focus_player_id: str | None = None
    intent: Intent
    selected_event_id: str | None = Field(default=None, pattern=r"^e_[a-z0-9_]+$")
    question_id: str | None = Field(default=None, pattern=r"^q_[a-z0-9_]+$")
    question_text: str | None = Field(default=None, max_length=300)
    agent: AgentRole

    @model_validator(mode="after")
    def validate_bound_values(self) -> "BoundContext":
        if self.evidence_cutoff_ms > self.cutoff_ms:
            raise ValueError("evidenceCutoffMs cannot exceed cutoffMs")
        if self.question_text is not None:
            cleaned = " ".join(self.question_text.split())
            if len(cleaned) > 300:
                raise ValueError("questionText exceeds 300 characters")
            object.__setattr__(self, "question_text", cleaned)
        expected = "referee" if self.intent in {"decision", "referee_question"} else "analyst"
        if self.agent in {"analyst", "referee"} and self.agent != expected:
            raise ValueError("Tool-capable agent does not match request intent")
        return self

    @property
    def allowed_tools(self) -> frozenset[str]:
        if self.agent == "analyst":
            return ANALYST_TOOLS | ({"find_similar_events"} if self.intent == "compare" else set())
        if self.agent == "referee":
            return REFEREE_TOOLS
        return frozenset()

    @property
    def max_tool_calls(self) -> int:
        return 5 if self.agent == "referee" else 6

    def for_agent(self, agent: AgentRole) -> "BoundContext":
        return BoundContext.model_validate({**self.model_dump(mode="python"), "agent": agent})


class EvidenceFact(ContractModel):
    id: str = Field(pattern=r"^f[0-9]{1,3}$")
    text: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(min_length=1, max_length=16)


class BriefStatistic(ContractModel):
    stat_ref: str = Field(min_length=1, max_length=120)
    value: int | float | None
    display: str | None = None


class EvidenceFactor(ContractModel):
    id: str = Field(pattern=r"^k[0-9]{1,3}$")
    text: str = Field(min_length=1, max_length=240)
    fact_ids: list[str] = Field(min_length=1, max_length=12)
    strength: Literal["supported", "plausible"]


class Significance(ContractModel):
    established: bool
    text: str = Field(default="", max_length=400)
    fact_ids: list[str] = Field(default_factory=list, max_length=12)


class DecisionRuling(ContractModel):
    decision_event_id: str = Field(pattern=r"^e_[a-z0-9_]+$")
    decision_type: Literal["offside", "tactical_foul_yellow", "penalty"]
    recorded_ruling: str = Field(min_length=1, max_length=80)
    law_ids: list[str] = Field(default_factory=list, max_length=4)
    requirement: str = Field(default="", max_length=400)
    data_finding: Literal["supports", "contradicts", "not_settled"]
    geometry: dict[str, Any] | None = None
    finding_reason: str = Field(default="", max_length=400)


class EvidenceBrief(ContractModel):
    brief_id: str = Field(pattern=r"^b_[a-f0-9]{8,64}$")
    intent: Intent
    event_id: str | None = Field(default=None, pattern=r"^e_[a-z0-9_]+$")
    facts: list[EvidenceFact] = Field(default_factory=list, max_length=24)
    stats: list[BriefStatistic] = Field(default_factory=list, max_length=12)
    factors: list[EvidenceFactor] = Field(default_factory=list, max_length=12)
    significance: Significance
    uncertainty: list[str] = Field(default_factory=list, max_length=12)
    involvement: dict[str, list[str]] = Field(default_factory=dict)
    comparison: dict[str, Any] | None = None
    ruling: DecisionRuling | None = None
    scope: Literal["in_scope", "redirect", "general_law", "out_of_scope"] = "in_scope"
    redirect_event_id: str | None = None


class Claim(ContractModel):
    id: str = Field(min_length=1, max_length=32)
    kind: Literal["fact", "number", "interpretation"]
    text: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=16)
    stat_ref: str | None = None
    geometry_ref: str | None = None
    value: int | float | None = None

    @model_validator(mode="after")
    def validate_number_reference(self) -> "Claim":
        if self.kind == "number":
            if self.value is None or bool(self.stat_ref) == bool(self.geometry_ref):
                raise ValueError("Number claims need a value and exactly one statRef or geometryRef")
        elif self.stat_ref or self.geometry_ref or self.value is not None:
            raise ValueError("Only number claims may carry numeric references")
        return self


class FocusPlayer(ContractModel):
    player_id: str = Field(pattern=r"^p_[a-z0-9_]+$")
    involved: bool
    event_ids: list[str] = Field(default_factory=list, max_length=12)
    text: str = Field(default="", max_length=240)


class RuleExplanation(ContractModel):
    rule_id: str
    text: str = Field(min_length=1, max_length=400)


class Explanation(ContractModel):
    headline: str = Field(min_length=1, max_length=90)
    what_happened: str = Field(min_length=1, max_length=600)
    factors: list[EvidenceFactor] = Field(default_factory=list, max_length=6)
    why_it_matters: str = Field(default="", max_length=400)
    significance_established: bool = True
    comparison: dict[str, Any] | None = None
    focus_player: FocusPlayer | None = None
    uncertainty: str = Field(default="", max_length=240)
    rule: RuleExplanation | None = None
    claims: list[Claim] = Field(default_factory=list, max_length=32)


class LawSummary(ContractModel):
    law_ids: list[str] = Field(default_factory=list, max_length=4)
    text: str = Field(min_length=1, max_length=400)


class DataFinding(ContractModel):
    finding: Literal["supports", "contradicts", "not_settled"]
    text: str = Field(min_length=1, max_length=400)


class RefereeAnswer(ContractModel):
    headline: str = Field(min_length=1, max_length=90)
    decision: str = Field(min_length=1, max_length=200)
    law: LawSummary
    data_shows: DataFinding
    uncertainty: str = Field(default="", max_length=240)
    suggested_follow_ups: list[str] = Field(default_factory=list, max_length=3)
    claims: list[Claim] = Field(default_factory=list, max_length=32)


class CriticIssue(ContractModel):
    sentence: int = Field(ge=0)
    kind: Literal["unsupported", "hedging", "level"]
    note: str = Field(min_length=1, max_length=300)


class CriticVerdict(ContractModel):
    verdict: Literal["pass", "revise_wording", "unsupported_claim"]
    issues: list[CriticIssue] = Field(default_factory=list, max_length=20)


class ToolError(ContractModel):
    code: Literal[
        "unknown_tool", "invalid_arguments", "not_visible", "not_found",
        "unsupported_incident", "tool_limit_reached", "internal",
    ]
    message: str = Field(min_length=1, max_length=300)


class ToolResultEnvelope(ContractModel):
    ok: bool
    data: dict[str, Any] | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=64)
    stat_refs: list[str] = Field(default_factory=list, max_length=32)
    note: str | None = None
    error: ToolError | None = None

    @model_validator(mode="after")
    def validate_envelope(self) -> "ToolResultEnvelope":
        if self.ok and (self.data is None or self.error is not None):
            raise ValueError("Successful tool result needs data and no error")
        if not self.ok and self.error is None:
            raise ValueError("Failed tool result needs an error")
        return self


class AgentEvidenceBrief(ContractModel):
    """Deprecated transport compatibility for the first Core scaffold."""

    headline: str = Field(min_length=1, max_length=180)
    summary: str = Field(min_length=1, max_length=1200)
    facts: list[str] = Field(default_factory=list, max_length=12)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)


class AgentRequest(ContractModel):
    schema_version: Literal[1] = 1
    request_id: str = Field(pattern=r"^req_[a-zA-Z0-9_-]{1,80}$")
    intent: Intent
    match_id: str = Field(pattern=r"^m_[a-z0-9_]+$")
    dataset_version: str = Field(min_length=1, max_length=32)
    cutoff_ms: int = Field(ge=0)
    evidence_cutoff_ms: int = Field(ge=0)
    event_id: str | None = None
    question_id: str | None = None
    question_text: str | None = Field(default=None, max_length=300)
    viewer: AgentViewer = Field(default_factory=AgentViewer)
    context: BoundContext | None = None
    evidence_brief: AgentEvidenceBrief | None = None

    @model_validator(mode="after")
    def bind_immutable_context(self) -> "AgentRequest":
        derived_agent: AgentRole = "referee" if self.intent in {"decision", "referee_question"} else "analyst"
        if self.context is None:
            object.__setattr__(self, "context", BoundContext(
                request_id=self.request_id,
                match_id=self.match_id,
                dataset_version=self.dataset_version,
                rules_version="1.2.0",
                cutoff_ms=self.cutoff_ms,
                evidence_cutoff_ms=self.evidence_cutoff_ms,
                level=self.viewer.level,
                language=self.viewer.language,
                perspective_team_id=self.viewer.club_id,
                focus_player_id=self.viewer.focus_player_id,
                intent=self.intent,
                selected_event_id=self.event_id,
                question_id=self.question_id,
                question_text=self.question_text,
                agent=derived_agent,
            ))
        else:
            context = self.context
            if (
                context.request_id != self.request_id
                or context.match_id != self.match_id
                or context.dataset_version != self.dataset_version
                or context.cutoff_ms != self.cutoff_ms
                or context.evidence_cutoff_ms != self.evidence_cutoff_ms
                or context.intent != self.intent
                or context.selected_event_id != self.event_id
                or context.question_id != self.question_id
                or context.question_text != self.question_text
                or context.level != self.viewer.level
                or context.language != self.viewer.language
                or context.perspective_team_id != self.viewer.club_id
                or context.focus_player_id != self.viewer.focus_player_id
            ):
                raise ValueError("AgentRequest fields do not match its immutable BoundContext")
        return self


class AgentResponse(ContractModel):
    schema_version: Literal[1] = 1
    request_id: str = Field(pattern=r"^req_[a-zA-Z0-9_-]{1,80}$")
    status: Literal["ok", "fallback"]
    source: Literal["generated", "fake", "template"]
    workflow_version: str = Field(min_length=1, max_length=32)
    answer: str = Field(min_length=1, max_length=2400)
    evidence_ids: list[str] = Field(default_factory=list, max_length=24)
    evidence_brief: EvidenceBrief | None = None
    explanation: Explanation | None = None
    referee_answer: RefereeAnswer | None = None

    @model_validator(mode="after")
    def output_matches_intent_shape(self) -> "AgentResponse":
        if self.explanation is not None and self.referee_answer is not None:
            raise ValueError("Response cannot contain both explanation and referee answer")
        return self
