"""Entry point shared by the hosted workflow and the evaluation harness."""

from __future__ import annotations

import os
from functools import lru_cache

from next_whistle_shared.contracts.agent import AgentRequest, AgentResponse
from next_whistle_shared.evidence.dataset import EvidenceDataset

from .orchestrator import WORKFLOW_VERSION, WorkflowResult, run_workflow
from .scripted import ScriptedRunner
from .workflow import _load_dataset


@lru_cache(maxsize=1)
def dataset() -> EvidenceDataset:
    return _load_dataset()


def critic_enabled() -> bool:
    return os.getenv("CRITIC_ENABLED", "false").lower() == "true"


async def run_request(payload: AgentRequest) -> WorkflowResult:
    """AGENT_MODE=fake (default) never calls a model. AGENT_MODE=foundry calls the configured deployments."""
    if os.getenv("AGENT_MODE", "fake").lower() == "foundry":
        from .foundry import FoundryRunner

        async with FoundryRunner.from_env() as runner:
            return await run_workflow(payload, dataset(), runner, critic_enabled=critic_enabled())
    return await run_workflow(payload, dataset(), ScriptedRunner(), critic_enabled=critic_enabled())


async def answer(payload: AgentRequest) -> AgentResponse:
    try:
        return (await run_request(payload)).response
    except Exception:
        return AgentResponse(
            request_id=payload.request_id, status="fallback", source="template", workflow_version=WORKFLOW_VERSION,
            answer="The recorded match facts are available, but a written explanation could not be produced just now.",
        )
