from datetime import date

from pydantic import BaseModel, ConfigDict, Field
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
