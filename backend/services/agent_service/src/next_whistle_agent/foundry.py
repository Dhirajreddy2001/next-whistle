"""Foundry-backed runner (Microsoft Agent Framework). Calls a real model: it incurs charges.

The dispatcher stays the only way to retrieve evidence: each allowed tool is a thin wrapper around
`ToolDispatcher.call`, and none of the tool schemas expose the match, the cutoff or the dataset version.
"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack
from typing import Any

from agent_framework import Agent, tool

from next_whistle_shared.evidence.dispatcher import ToolDispatcher
from next_whistle_shared.evidence.tool_schemas import TOOL_ARGUMENT_MODELS, tool_json_schemas

from .runner import RunContext, T, Usage
from .scripted import RunnerError

TOOL_TEMPERATURE, WRITER_TEMPERATURE = 0.2, 0.5
TOKEN_CAPS = {"analyst": 14_000, "referee": 12_000, "storyteller": 3_000, "critic": 2_500}


def build_tools(dispatcher: ToolDispatcher) -> list[Any]:
    """One Agent Framework tool per allowed evidence tool, executed through the bound dispatcher."""
    descriptions = {item["name"]: item["description"] for item in tool_json_schemas()}
    tools = []
    for name in sorted(dispatcher.context.allowed_tools & set(TOOL_ARGUMENT_MODELS)):
        async def call(_name: str = name, **arguments: Any) -> str:
            return dispatcher.call(_name, arguments).model_dump_json(by_alias=True)
        call.__name__ = name
        tools.append(tool(call, name=name, description=descriptions[name], schema=TOOL_ARGUMENT_MODELS[name]))
    return tools


class FoundryRunner:
    source = "generated"

    def __init__(self, endpoint: str, tools_model: str, small_model: str):
        self.endpoint, self.tools_model, self.small_model = endpoint, tools_model, small_model
        self.usage = Usage()
        self._stack: AsyncExitStack | None = None
        self._credential: Any = None
        self._clients: dict[str, Any] = {}

    @classmethod
    def from_env(cls) -> "FoundryRunner":
        endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
        tools_model = os.getenv("MODEL_TOOLS") or os.environ["FOUNDRY_MODEL"]
        return cls(endpoint, tools_model, os.getenv("MODEL_SMALL") or tools_model)

    async def __aenter__(self) -> "FoundryRunner":
        from azure.identity.aio import DefaultAzureCredential

        self._stack = AsyncExitStack()
        self._credential = await self._stack.enter_async_context(DefaultAzureCredential())
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._stack is not None:
            await self._stack.aclose()

    async def _client(self, model: str) -> Any:
        if self._stack is None:
            raise RuntimeError("Use FoundryRunner as an async context manager.")
        if model not in self._clients:
            from agent_framework.foundry import FoundryChatClient

            self._clients[model] = await self._stack.enter_async_context(
                FoundryChatClient(project_endpoint=self.endpoint, model=model, credential=self._credential)
            )
        return self._clients[model]

    async def _run(self, role: str, model: str, instructions: str, user_input: str, output_type: type[T], tools: list[Any] | None, temperature: float) -> T:
        agent = Agent(
            client=await self._client(model), instructions=instructions, tools=tools or None,
            default_options={"response_format": output_type, "temperature": temperature, "max_tokens": TOKEN_CAPS[role]},
        )
        try:
            result = await agent.run(user_input)
        except Exception as error:  # surfaced as a workflow fallback, never a crash
            raise RunnerError(f"{role} model call failed: {type(error).__name__}") from error
        details = getattr(result, "usage_details", None) or {}
        self.usage.add(details.get("input_token_count") or 0, details.get("output_token_count") or 0)
        value = getattr(result, "value", None)
        if isinstance(value, output_type):
            return value
        try:
            return output_type.model_validate_json(result.text)
        except Exception as error:
            raise RunnerError(f"{role} returned output that does not match {output_type.__name__}") from error

    async def run_tool_agent(self, *, role: str, instructions: str, user_input: str, dispatcher: ToolDispatcher, output_type: type[T], run: RunContext) -> T:
        return await self._run(role, self.tools_model, instructions, user_input, output_type, build_tools(dispatcher), TOOL_TEMPERATURE)

    async def run_writer(self, *, role: str, instructions: str, user_input: str, output_type: type[T], run: RunContext) -> T:
        temperature = WRITER_TEMPERATURE if role == "storyteller" else TOOL_TEMPERATURE
        return await self._run(role, self.small_model, instructions, user_input, output_type, None, temperature)
