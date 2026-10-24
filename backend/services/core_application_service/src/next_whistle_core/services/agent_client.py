"""Small client for the separately deployed Agent Service Responses endpoint."""

import json

import httpx

from next_whistle_shared.contracts.agent import AgentRequest, AgentResponse


class AgentServiceClient:
    def __init__(self, base_url: str, timeout_seconds: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def generate(self, payload: AgentRequest) -> AgentResponse:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/responses",
                json={"input": json.dumps(payload.model_dump(mode="json", by_alias=True)), "store": False},
            )
            response.raise_for_status()
        body = response.json()
        text = body.get("output_text")
        if not text:
            text = "".join(
                part.get("text", "")
                for item in body.get("output", [])
                for part in item.get("content", [])
                if part.get("type") in {"output_text", "text"}
            )
        result = AgentResponse.model_validate_json(text)
        if result.request_id != payload.request_id:
            raise ValueError("Agent response requestId mismatch")
        allowed = set(payload.evidence_brief.evidence_ids)
        if not set(result.evidence_ids).issubset(allowed):
            raise ValueError("Agent response referenced unknown evidence")
        return result
