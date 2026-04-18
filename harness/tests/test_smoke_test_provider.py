"""Unit tests for SmokeTestProvider — schema-aware embedded provider."""

from __future__ import annotations

import json

import pytest

from harness.providers.smoke_test_provider import (
    SmokeTestProvider,
    generate_stub_from_schema,
    _generate_stub_value,
    _extract_schema_from_system_prompt,
)
from harness.tests.genesis_test_provider import GENESIS_OUTPUTS


class TestSchemaStubGenerator:
    """Test the stub value generation from JSON schemas."""

    def test_string_stub(self):
        assert _generate_stub_value({"type": "string"}) == "stub"

    def test_string_enum_uses_first(self):
        assert _generate_stub_value({"type": "string", "enum": ["alpha", "beta"]}) == "alpha"

    def test_integer_stub(self):
        assert _generate_stub_value({"type": "integer"}) == 1

    def test_number_stub(self):
        assert _generate_stub_value({"type": "number"}) == 1.0

    def test_boolean_stub(self):
        assert _generate_stub_value({"type": "boolean"}) is True

    def test_array_stub(self):
        result = _generate_stub_value({"type": "array", "items": {"type": "string"}})
        assert result == ["stub"]

    def test_array_nested_objects(self):
        result = _generate_stub_value({
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "count": {"type": "integer"}},
            },
        })
        assert result == [{"name": "stub", "count": 1}]

    def test_object_stub(self):
        result = _generate_stub_value({
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "active": {"type": "boolean"},
            },
        })
        assert result == {"title": "stub", "active": True}

    def test_empty_object_stub(self):
        assert _generate_stub_value({"type": "object"}) == {}

    def test_unknown_type_returns_stub(self):
        assert _generate_stub_value({"type": "null"}) == "stub"


class TestGenerateStubFromSchema:
    """Test full schema → stub object generation."""

    def test_simple_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["status"],
        }
        result = generate_stub_from_schema(schema)
        assert result["status"] == "stub"
        assert result["count"] == 1

    def test_empty_schema_returns_fallback(self):
        result = generate_stub_from_schema({})
        assert "status" in result
        assert result["status"] == "completed"

    def test_none_schema_returns_fallback(self):
        result = generate_stub_from_schema(None)
        assert result["status"] == "completed"

    def test_required_fields_always_present(self):
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a", "b"],
        }
        result = generate_stub_from_schema(schema)
        assert "a" in result
        assert "b" in result


class TestSchemaExtraction:
    """Test extracting output_schema from system prompts."""

    def test_extracts_from_required_output_format(self):
        prompt = """You are the CodeWriterAgent.

## Required Output Format
```json
{"type": "object", "properties": {"files_created": {"type": "array", "items": {"type": "string"}}, "summary": {"type": "string"}}}
```

Do your best work.
"""
        schema = _extract_schema_from_system_prompt(prompt)
        assert schema is not None
        assert "properties" in schema
        assert "files_created" in schema["properties"]

    def test_returns_none_for_no_schema(self):
        prompt = "You are a helpful agent. Do your best."
        assert _extract_schema_from_system_prompt(prompt) is None

    def test_handles_malformed_json(self):
        prompt = """## Required Output Format
```json
{not valid json}
```
"""
        assert _extract_schema_from_system_prompt(prompt) is None


class TestSmokeTestProvider:
    """Test the SmokeTestProvider end-to-end."""

    @pytest.mark.asyncio
    async def test_genesis_agents_delegate_to_genesis_provider(self):
        """Genesis agents should return structured JSON from GenesisTestProvider."""
        provider = SmokeTestProvider()
        for agent_name in list(GENESIS_OUTPUTS.keys())[:3]:
            messages = [
                {"role": "system", "content": f"You are the {agent_name}. Do your work."},
                {"role": "user", "content": "Run genesis."},
            ]
            response = await provider.chat(messages)
            content = response["content"]
            assert content, f"Empty content for {agent_name}"
            parsed = json.loads(content)
            assert isinstance(parsed, dict), f"Non-dict for {agent_name}"

    @pytest.mark.asyncio
    async def test_domain_agent_returns_schema_correct_output(self):
        """Domain agents with output_schema should return valid stubs."""
        provider = SmokeTestProvider()
        schema = {
            "type": "object",
            "properties": {
                "files_created": {"type": "array", "items": {"type": "string"}},
                "summary": {"type": "string"},
            },
            "required": ["files_created", "summary"],
        }

        prompt = f"""You are the CodeWriterAgent.

## Required Output Format
```json
{json.dumps(schema)}
```
"""
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Write code."},
        ]
        response = await provider.chat(messages)
        parsed = json.loads(response["content"])
        assert "files_created" in parsed
        assert "summary" in parsed
        assert isinstance(parsed["files_created"], list)

    @pytest.mark.asyncio
    async def test_domain_agent_with_kwarg_schema(self):
        """Domain agents can receive output_schema via kwargs."""
        provider = SmokeTestProvider()
        schema = {
            "type": "object",
            "properties": {"result": {"type": "string"}, "score": {"type": "number"}},
        }
        messages = [
            {"role": "system", "content": "You are the ReviewerAgent."},
            {"role": "user", "content": "Review code."},
        ]
        response = await provider.chat(messages, output_schema=schema)
        parsed = json.loads(response["content"])
        assert "result" in parsed
        assert "score" in parsed

    @pytest.mark.asyncio
    async def test_unknown_agent_returns_fallback(self):
        """Unknown agents should get a graceful fallback."""
        provider = SmokeTestProvider()
        messages = [
            {"role": "system", "content": "You are a mystery agent."},
            {"role": "user", "content": "Do something."},
        ]
        response = await provider.chat(messages)
        parsed = json.loads(response["content"])
        assert "status" in parsed
        assert parsed["status"] == "completed"

    @pytest.mark.asyncio
    async def test_zero_tokens(self):
        """All responses should report zero token usage."""
        provider = SmokeTestProvider()
        messages = [
            {"role": "system", "content": "You are the TestRunnerAgent."},
            {"role": "user", "content": "Run tests."},
        ]
        response = await provider.chat(messages)
        assert response["tokens_used"] == 0

    @pytest.mark.asyncio
    async def test_empty_tool_calls(self):
        """All responses should have empty tool_calls."""
        provider = SmokeTestProvider()
        messages = [
            {"role": "system", "content": "You are the LinterAgent."},
            {"role": "user", "content": "Lint code."},
        ]
        response = await provider.chat(messages)
        assert response["tool_calls"] == []

    @pytest.mark.asyncio
    async def test_call_log_tracks_sources(self):
        """Call log should track which source was used for each call."""
        provider = SmokeTestProvider()

        # Genesis agent
        await provider.chat([
            {"role": "system", "content": "You are the SourceScannerAgent."},
            {"role": "user", "content": "Scan."},
        ])

        # Unknown agent
        await provider.chat([
            {"role": "system", "content": "You are a random agent."},
            {"role": "user", "content": "Go."},
        ])

        assert len(provider.call_log) == 2
        assert provider.call_log[0]["source"] == "genesis_provider"
        assert provider.call_log[1]["source"] == "fallback"

    @pytest.mark.asyncio
    async def test_stream_yields_single_chunk(self):
        """Stream should yield exactly one chunk."""
        provider = SmokeTestProvider()
        messages = [
            {"role": "system", "content": "You are the TestRunnerAgent."},
            {"role": "user", "content": "Run."},
        ]
        chunks = []
        async for chunk in provider.stream(messages):
            chunks.append(chunk)
        assert len(chunks) == 1
        assert chunks[0].is_final is True
        assert chunks[0].tokens_used == 0

    def test_provider_name(self):
        provider = SmokeTestProvider()
        assert provider.provider_name == "smoke_test"

    def test_default_model(self):
        provider = SmokeTestProvider()
        assert provider.default_model == "smoke-test"
