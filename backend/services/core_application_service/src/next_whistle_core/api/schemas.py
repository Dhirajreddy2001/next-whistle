from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class TeamSummary(ApiModel):
    team_id: str = Field(pattern=r"^t_[a-z0-9_]+$")
    name: str
    short_name: str = Field(min_length=3, max_length=3)
    colors: list[str] = Field(min_length=2, max_length=2)


class MatchSummary(ApiModel):
    match_id: str = Field(pattern=r"^m_[a-z0-9_]+$")
    label: str
    kickoff_date: date
    duration_ms: int
    home: TeamSummary
    away: TeamSummary


class MatchCatalog(ApiModel):
    dataset_version: str
    matches: list[MatchSummary]


class LiveMatchState(ApiModel):
    instance_id: str = Field(pattern=r"^li_[a-z0-9_]+$")
    match_id: str = Field(pattern=r"^m_[a-z0-9_]+$")
    status: str = "live"
    server_time_ms: int
    instance_start_ms: int
    match_ms: int
    duration_ms: int
    cycle_number: int


class ScheduleScore(ApiModel):
    home: int
    away: int


class ScheduleMoment(ApiModel):
    minute: int
    kind: str
    team: str


class ScheduleItem(ApiModel):
    slot: int
    kind: Literal["live", "replay", "result"]
    starts_at_ms: int
    starts_at: str
    duration_ms: int
    match_id: str | None = Field(default=None, pattern=r"^m_[a-z0-9_]+$")
    home: TeamSummary
    away: TeamSummary
    live_match_ms: int | None = None
    score: ScheduleScore | None = None
    key_moments: list[ScheduleMoment] | None = None


class Schedule(ApiModel):
    generated_at_ms: int
    items: list[ScheduleItem]


class RefreshSessionRequest(ApiModel):
    refresh_token: str = Field(min_length=32, max_length=256)


class GenerationRequest(ApiModel):
    cutoff_ms: int = Field(ge=0, alias="cutoffMs")
    event_id: str | None = Field(default=None, pattern=r"^e_[a-z0-9_]+$")
    intent: str = "explain"
    language: str = Field(default="en", min_length=2, max_length=12)
    level: Literal["beginner", "intermediate", "pro"] = "beginner"


class QuestionTurnIn(ApiModel):
    question: str = Field(min_length=1, max_length=300)
    answer_headline: str = Field(default="", max_length=120, alias="answerHeadline")


class RefereeQuestionRequest(ApiModel):
    cutoff_ms: int = Field(ge=0, alias="cutoffMs")
    event_id: str | None = Field(default=None, pattern=r"^e_[a-z0-9_]+$", alias="eventId")
    question_id: str | None = Field(default=None, pattern=r"^q_[a-z0-9_]+$", alias="questionId")
    question_text: str | None = Field(default=None, min_length=1, max_length=300, alias="questionText")
    history: list[QuestionTurnIn] = Field(default_factory=list, max_length=2)
    language: str = Field(default="en", min_length=2, max_length=12)
    level: Literal["beginner", "intermediate", "pro"] = "beginner"

    @model_validator(mode="after")
    def require_one_question(self):
        if bool(self.question_id) == bool(self.question_text):
            raise ValueError("Provide exactly one of questionId or questionText")
        if self.question_id and not self.event_id:
            raise ValueError("A suggested question needs an eventId")
        return self
