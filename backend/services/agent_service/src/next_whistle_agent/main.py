"""Responses host for the native Microsoft Agent Framework workflow."""

import json

from agent_framework import WorkflowBuilder, WorkflowContext, executor
from agent_framework_foundry_hosting import HostedResponseRequest, ResponsesHostServer, WorkflowTurn

from next_whistle_shared.contracts.agent import AgentRequest
from next_whistle_agent.service import answer


async def parse_response(request: HostedResponseRequest) -> WorkflowTurn[AgentRequest]:
    """Accept one Core-owned contract as the current Responses turn."""
    text = await request.get_input_text()
    if not text:
        raise ValueError("Responses input must contain an AgentRequest JSON object")
    return WorkflowTurn(input=AgentRequest.model_validate_json(text))


def build_workflow(_request: HostedResponseRequest):
    """Build a fresh graph per request: the host rejects executors shared between requests."""

    @executor(id="evidence_answer")
    async def answer_with_evidence(payload: AgentRequest, ctx: WorkflowContext[str]) -> None:
        """Route the request through the role workflow; every fact comes from cutoff-bound evidence tools."""
        response = await answer(payload)
        await ctx.yield_output(json.dumps(response.model_dump(mode="json", by_alias=True)))

    return WorkflowBuilder(start_executor=answer_with_evidence).build()


def main() -> None:
    ResponsesHostServer(workflow=build_workflow, parse_response=parse_response).run()


if __name__ == "__main__":
    main()
