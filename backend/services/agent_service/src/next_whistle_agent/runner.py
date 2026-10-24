"""Model runner interface. The orchestrator talks to roles only through this protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from pydantic import BaseModel

from next_whistle_shared.contracts.agent import AgentRequest, BoundContext, EvidenceBrief
from next_whistle_shared.evidence.dispatcher import ToolDispatcher

T = TypeVar("T", bound=BaseModel)


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens


@dataclass
class RunContext:
    """What a role may see: the bound context, the request and any validation failures to fix.

    A model-backed runner reads only `instructions` and `user_input`; the scripted runner also reads this.
    """

    context: BoundContext
    payload: AgentRequest
    failures: list[str] = field(default_factory=list)
    brief: EvidenceBrief | None = None
    answer: BaseModel | None = None


class AgentRunner(Protocol):
    source: str  # "fake" or "generated"
    usage: Usage

    async def run_tool_agent(
        self, *, role: str, instructions: str, user_input: str, dispatcher: ToolDispatcher,
        output_type: type[T], run: RunContext,
    ) -> T: ...

    async def run_writer(
        self, *, role: str, instructions: str, user_input: str, output_type: type[T], run: RunContext,
    ) -> T: ...
