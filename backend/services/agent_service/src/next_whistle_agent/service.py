"""Entry point shared by the hosted workflow and the evaluation harness."""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from next_whistle_shared.contracts.agent import AgentRequest, AgentResponse, TokenUsage
from next_whistle_shared.evidence.dataset import EvidenceDataset

from .orchestrator import WORKFLOW_VERSION, WorkflowResult, run_workflow
from .scripted import ScriptedRunner
from .workflow import _load_dataset


log = logging.getLogger("next_whistle_agent")


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
            result = await run_workflow(payload, dataset(), runner, critic_enabled=critic_enabled())
    else:
        runner = ScriptedRunner()
        result = await run_workflow(payload, dataset(), runner, critic_enabled=critic_enabled())
    usage = TokenUsage(input_tokens=runner.usage.input_tokens, output_tokens=runner.usage.output_tokens)
    result.response = result.response.model_copy(update={"usage": usage})
    # One line per request, so a "facts only" answer always has a visible reason (throttled, invalid output, ...).
    log.info(
        "workflow intent=%s level=%s status=%s source=%s fallback=%s retries=%d model_calls=%d tokens_in=%d tokens_out=%d brief_cached=%s",
        payload.intent, payload.viewer.level, result.response.status, result.response.source, result.trace.fallback_reason or "-",
        result.trace.retries, runner.usage.calls, usage.input_tokens, usage.output_tokens, result.response.brief_cached,
    )
    return result


async def answer(payload: AgentRequest) -> AgentResponse:
    try:
        return (await run_request(payload)).response
    except Exception:
        log.exception("workflow crashed; serving the factual fallback")
        return AgentResponse(
            request_id=payload.request_id, status="fallback", source="template", workflow_version=WORKFLOW_VERSION,
            answer="The recorded match facts are available, but a written explanation could not be produced just now.",
        )
