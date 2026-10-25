"""Foundry-backed runner (Microsoft Agent Framework). Calls a real model: it incurs charges.

The dispatcher stays the only way to retrieve evidence: each allowed tool is a thin wrapper around
`ToolDispatcher.call`, and none of the tool schemas expose the match, the cutoff or the dataset version.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import AsyncExitStack
from typing import Any

from agent_framework import Agent, tool

from next_whistle_shared.evidence.dispatcher import ToolDispatcher
from next_whistle_shared.evidence.tool_schemas import TOOL_ARGUMENT_MODELS, tool_json_schemas

from .prefetch import is_free_text, prefetch
from .runner import RunContext, T, Usage
from .scripted import InvalidOutput, RunnerError

TOOL_TEMPERATURE, WRITER_TEMPERATURE = 0.2, 0.5
CALL_TIMEOUT_SECONDS = float(os.getenv("MODEL_CALL_TIMEOUT_SECONDS", "45"))  # one stalled model call must not hold a request forever
TOKEN_CAPS = {"analyst": 14_000, "referee": 12_000, "storyteller": 3_000, "critic": 2_500}


GATHER_SUFFIX = (
    "\n\nWork in steps: call the evidence tools you need, one after another, starting with the selected event. "
    "When you have what you need, reply with the single word DONE. Do not write the brief yet."
)
WRITE_SUFFIX = (
    "\n\nThe evidence has already been retrieved for you, below. Do not call tools. Write the brief now, citing only "
    "evidence IDs and statRefs that appear in that evidence."
)
MAX_EVIDENCE_CHARS = 24_000


def build_tools(dispatcher: ToolDispatcher, transcript: list[dict[str, Any]] | None = None) -> list[Any]:
    """One Agent Framework tool per allowed evidence tool, executed through the bound dispatcher."""
    descriptions = {item["name"]: item["description"] for item in tool_json_schemas()}
    tools = []
    for name in sorted(dispatcher.context.allowed_tools & set(TOOL_ARGUMENT_MODELS)):
        async def call(_name: str = name, **arguments: Any) -> str:
            text = dispatcher.call(_name, arguments).model_dump_json(by_alias=True)
            if transcript is not None:
                transcript.append({"tool": _name, "arguments": arguments, "result": json.loads(text)})
            return text
        call.__name__ = name
        tools.append(tool(call, name=name, description=descriptions[name], schema=TOOL_ARGUMENT_MODELS[name]))
    return tools


class FoundryRunner:
    source = "generated"

    def __init__(self, endpoint: str | None, tools_model: str, small_model: str, base_url: str | None = None):
        # `base_url` (https://<account>.services.ai.azure.com/openai/v1) calls the model deployments directly; otherwise the
        # Foundry project endpoint is used.
        self.endpoint, self.tools_model, self.small_model, self.base_url = endpoint, tools_model, small_model, base_url
        self.usage = Usage()
        self._stack: AsyncExitStack | None = None
        self._credential: Any = None
        self._clients: dict[str, Any] = {}

    @classmethod
    def from_env(cls) -> "FoundryRunner":
        endpoint, base_url = os.getenv("FOUNDRY_PROJECT_ENDPOINT"), os.getenv("FOUNDRY_OPENAI_BASE_URL")
        if not endpoint and not base_url:
            raise KeyError("Set FOUNDRY_OPENAI_BASE_URL (direct) or FOUNDRY_PROJECT_ENDPOINT")
        tools_model = os.getenv("MODEL_TOOLS") or os.environ["FOUNDRY_MODEL"]
        return cls(endpoint, tools_model, os.getenv("MODEL_SMALL") or tools_model, base_url)

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
            # The clients wrap the credential and have nothing of their own to close; the credential is closed on exit.
            if self.base_url:
                from agent_framework.openai import OpenAIChatCompletionClient

                self._clients[model] = OpenAIChatCompletionClient(model=model, base_url=self.base_url, credential=self._credential)
            else:
                from agent_framework.foundry import FoundryChatClient

                self._clients[model] = FoundryChatClient(project_endpoint=self.endpoint, model=model, credential=self._credential)
        return self._clients[model]

    async def _call(self, role: str, model: str, instructions: str, user_input: str, tools: list[Any] | None, temperature: float, response_format: type[T] | None) -> Any:
        options: dict[str, Any] = {"temperature": temperature, "max_tokens": TOKEN_CAPS[role]}
        if response_format is not None:
            options["response_format"] = response_format
        agent = Agent(client=await self._client(model), instructions=instructions, tools=tools or None, default_options=options)
        try:
            result = await asyncio.wait_for(agent.run(user_input), CALL_TIMEOUT_SECONDS)
        except Exception as error:  # surfaced as a workflow fallback, never a crash (a stalled call is a timeout)
            raise RunnerError(f"{role} model call failed: {type(error).__name__}") from error
        details = getattr(result, "usage_details", None) or {}
        self.usage.add(details.get("input_token_count") or 0, details.get("output_token_count") or 0)
        return result

    async def _structured(self, role: str, model: str, instructions: str, user_input: str, output_type: type[T], temperature: float) -> T:
        result = await self._call(role, model, instructions, user_input, None, temperature, output_type)
        try:
            value = result.value
            if isinstance(value, output_type):
                return value
            return output_type.model_validate_json(result.text)
        except Exception as error:
            reason = " ".join(str(error).split("\n")[1:3])[:200] or type(error).__name__
            raise InvalidOutput(f"{output_type.__name__}: {reason}") from error

    async def run_tool_agent(self, *, role: str, instructions: str, user_input: str, dispatcher: ToolDispatcher, output_type: type[T], run: RunContext) -> T:
        """Two phases: the evidence is gathered, then the model writes the structured brief from it.

        A free-text question lets the model gather with its own tools; the buttons use the standard set from `prefetch`.

        Asking for strict structured output and tools in the same call made DeepSeek answer in JSON immediately and
        skip the tools, so the phases are separate.
        """
        if is_free_text(run.payload):
            transcript: list[dict[str, Any]] = []
            await self._call(role, self.tools_model, instructions + GATHER_SUFFIX, user_input, build_tools(dispatcher, transcript), TOOL_TEMPERATURE, None)
        else:
            transcript = prefetch(dispatcher, run.context)  # the standard evidence needs no model round trips
        if not transcript:
            raise InvalidOutput("the model did not call any evidence tool")
        evidence = json.dumps(transcript, ensure_ascii=False)[:MAX_EVIDENCE_CHARS]
        return await self._structured(role, self.tools_model, instructions + WRITE_SUFFIX, f"{user_input}\n\nEvidence returned by the tools:\n{evidence}", output_type, TOOL_TEMPERATURE)

    async def run_writer(self, *, role: str, instructions: str, user_input: str, output_type: type[T], run: RunContext) -> T:
        temperature = WRITER_TEMPERATURE if role == "storyteller" else TOOL_TEMPERATURE
        return await self._structured(role, self.small_model, instructions, user_input, output_type, temperature)
