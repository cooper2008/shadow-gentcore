"""SmokeTestProvider — schema-aware embedded provider for smoke testing without API tokens.

Resolution order:
1. If agent matches GenesisTestProvider outputs → delegate (rich structured JSON)
2. Otherwise, extract output_schema from system prompt's '## Required Output Format'
   block → generate minimal valid JSON stubs (str→"stub", int→1, bool→true, array→[one item])
3. Fallback: {"status": "completed", "summary": "smoke test stub"}

Zero API tokens. Zero network calls. Zero env vars needed.
"""

from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator

from harness.providers.base_provider import BaseProvider, LLMChunk
from harness.tests.genesis_test_provider import GenesisTestProvider, GENESIS_OUTPUTS


# ── Schema stub generator ─────────────────────────────────────────────────

def _generate_stub_value(prop_schema: dict[str, Any]) -> Any:
    """Generate a minimal valid value for a JSON schema property."""
    prop_type = prop_schema.get("type", "string")

    if prop_type == "string":
        # Use enum first value if available
        if "enum" in prop_schema:
            return prop_schema["enum"][0]
        return "stub"
    elif prop_type == "integer":
        return 1
    elif prop_type == "number":
        return 1.0
    elif prop_type == "boolean":
        return True
    elif prop_type == "array":
        items_schema = prop_schema.get("items", {"type": "string"})
        return [_generate_stub_value(items_schema)]
    elif prop_type == "object":
        nested_props = prop_schema.get("properties", {})
        if nested_props:
            return {k: _generate_stub_value(v) for k, v in nested_props.items()}
        return {}
    return "stub"


def generate_stub_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Generate a minimal valid JSON object from a JSON schema.

    Populates all required fields (and optionally all properties) with stub values
    that pass OutputValidator type checks.
    """
    if not schema or not isinstance(schema, dict):
        return {"status": "completed", "summary": "smoke test stub"}

    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    result: dict[str, Any] = {}
    # Always include required fields; also include optional for completeness
    for field_name, field_schema in properties.items():
        result[field_name] = _generate_stub_value(field_schema)

    # Ensure all required fields present even if not in properties
    for req in required:
        if req not in result:
            result[req] = "stub"

    return result


def _extract_schema_from_system_prompt(system_prompt: str) -> dict[str, Any] | None:
    """Extract output_schema JSON from PromptAssembler's '## Required Output Format' block."""
    # PromptAssembler injects the schema as a JSON block after this heading
    pattern = r"##\s*Required Output Format\s*\n```(?:json)?\s*\n(.*?)\n```"
    match = re.search(pattern, system_prompt, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Fallback: look for any JSON schema-like object with "properties" key
    pattern2 = r'\{[^{}]*"properties"\s*:\s*\{.*?\}\s*\}'
    match2 = re.search(pattern2, system_prompt, re.DOTALL)
    if match2:
        try:
            candidate = json.loads(match2.group(0))
            if "properties" in candidate:
                return candidate
        except json.JSONDecodeError:
            pass

    return None


# ── Identify agent from messages ───────────────────────────────────────────

def _identify_genesis_agent(messages: list[dict[str, Any]]) -> str:
    """Check if any message identifies a known genesis agent."""
    for msg in messages:
        if msg.get("role") == "system":
            content = str(msg.get("content", ""))
            for agent_name in GENESIS_OUTPUTS:
                if agent_name in content:
                    return agent_name
    return ""


# ── SmokeTestProvider ──────────────────────────────────────────────────────

class SmokeTestProvider(BaseProvider):
    """Schema-aware embedded provider — zero API tokens.

    Uses GenesisTestProvider for genesis agents, generates schema-correct stubs
    for domain agents, and provides a sensible fallback for unknown agents.
    """

    def __init__(self) -> None:
        self._genesis_provider = GenesisTestProvider()
        self.call_log: list[dict[str, Any]] = []

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """Return schema-correct output without any API calls."""
        # 1. Check if this is a genesis agent → delegate
        genesis_agent = _identify_genesis_agent(messages)
        if genesis_agent:
            self.call_log.append({"agent": genesis_agent, "source": "genesis_provider"})
            return await self._genesis_provider.chat(messages, **kwargs)

        # 2. Try to extract output_schema from system prompt → generate stub
        system_prompt = ""
        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = str(msg.get("content", ""))
                break

        schema = _extract_schema_from_system_prompt(system_prompt)

        # Also check kwargs for output_schema (some callers pass it directly)
        if not schema and "output_schema" in kwargs:
            schema = kwargs["output_schema"]

        if schema:
            output = generate_stub_from_schema(schema)
            agent_hint = self._extract_agent_hint(system_prompt)
            self.call_log.append({"agent": agent_hint, "source": "schema_stub"})
            return {
                "content": json.dumps(output, indent=2),
                "tokens_used": 0,
                "tool_calls": [],
                "model": "smoke-test",
            }

        # 3. Fallback
        agent_hint = self._extract_agent_hint(system_prompt)
        self.call_log.append({"agent": agent_hint, "source": "fallback"})
        fallback = {"status": "completed", "summary": f"smoke test stub for {agent_hint}"}
        return {
            "content": json.dumps(fallback, indent=2),
            "tokens_used": 0,
            "tool_calls": [],
            "model": "smoke-test",
        }

    async def stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> AsyncIterator[LLMChunk]:
        """Yield a single smoke-test chunk."""
        response = await self.chat(messages, **kwargs)
        yield LLMChunk(
            content=response["content"],
            delta=response["content"],
            is_final=True,
            tokens_used=0,
        )

    @property
    def provider_name(self) -> str:
        return "smoke_test"

    @property
    def default_model(self) -> str:
        return "smoke-test"

    @staticmethod
    def _extract_agent_hint(system_prompt: str) -> str:
        """Extract agent name from the first line of the system prompt."""
        if not system_prompt:
            return "unknown"
        first_line = system_prompt.strip().split("\n")[0][:120]
        # Try to find an agent name pattern (PascalCase ending in Agent)
        match = re.search(r"\b([A-Z][a-zA-Z]+Agent)\b", first_line)
        if match:
            return match.group(1)
        return first_line[:60] if first_line else "unknown"
