"""Tests for H1 — always-on submit_output (provider + react integration)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from harness.providers.anthropic_provider import AnthropicProvider


class _FakeContentBlock:
    """Mimics an Anthropic SDK content block (text or tool_use)."""

    def __init__(self, text: str | None = None, tool_use: dict[str, Any] | None = None) -> None:
        if text is not None:
            self.text = text
        if tool_use is not None:
            self.type = "tool_use"
            self.id = tool_use["id"]
            self.name = tool_use["name"]
            self.input = tool_use["input"]


class _FakeUsage:
    input_tokens = 100
    output_tokens = 50


class _FakeResponse:
    def __init__(self, content_blocks: list[_FakeContentBlock]) -> None:
        self.content = content_blocks
        self.usage = _FakeUsage()
        self.stop_reason = "end_turn"
        self.id = "msg_fake_123"


class _FakeClient:
    """Records the last create() kwargs and returns a scripted response."""

    def __init__(self, response_blocks: list[_FakeContentBlock]) -> None:
        self._response_blocks = response_blocks
        self.last_kwargs: dict[str, Any] = {}
        # Anthropic SDK exposes .messages.create(...)
        self.messages = self

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.last_kwargs = kwargs
        return _FakeResponse(self._response_blocks)


def _provider_with_fake(fake: _FakeClient) -> AnthropicProvider:
    prov = AnthropicProvider(api_key="test", model="claude-sonnet-4-6-20250414")
    prov._client = fake  # inject fake, bypassing lazy init
    return prov


OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["summary"],
    "properties": {"summary": {"type": "string"}, "score": {"type": "number"}},
}


# ── Forced mode (schema + no tools) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_forced_mode_only_submit_output_tool() -> None:
    fake = _FakeClient([
        _FakeContentBlock(tool_use={
            "id": "tu_1", "name": "submit_output",
            "input": {"summary": "all good", "score": 0.9},
        }),
    ])
    prov = _provider_with_fake(fake)
    resp = await prov.chat([{"role": "user", "content": "hi"}], output_schema=OUTPUT_SCHEMA)

    # Only submit_output should be in the tools list; tool_choice should force it.
    sent_tools = fake.last_kwargs["tools"]
    assert len(sent_tools) == 1
    assert sent_tools[0]["name"] == "submit_output"
    assert fake.last_kwargs["tool_choice"]["name"] == "submit_output"

    # Response: content = JSON, tool_calls empty (submit_output stripped).
    parsed = json.loads(resp.content)
    assert parsed == {"summary": "all good", "score": 0.9}
    assert resp.tool_calls == []
    assert resp.raw["submit_output_fired"] is True


@pytest.mark.asyncio
async def test_empty_tools_list_treated_as_forced_mode() -> None:
    """Regression: `tools=[]` (empty list) must behave the same as `tools=None`.

    Pre-fix, an empty tools list fell through to coexist mode (tool_choice=auto)
    instead of forced mode. Vendors via Anthropic-compat (GLM/MiniMax) would
    then emit plain `{}` content instead of calling the only available tool —
    silently breaking single-shot agents like AgentArchitect/v2,
    ConflictResolver, and ContextEngineer that declare `tools: []` in their
    manifests by design (no iterative tool use; preload-only context).
    """
    fake = _FakeClient([
        _FakeContentBlock(tool_use={
            "id": "tu_1", "name": "submit_output",
            "input": {"summary": "ok", "score": 1.0},
        }),
    ])
    prov = _provider_with_fake(fake)
    resp = await prov.chat(
        [{"role": "user", "content": "hi"}],
        tools=[],  # explicitly empty — was the silent-failure path
        output_schema=OUTPUT_SCHEMA,
    )

    sent_tools = fake.last_kwargs["tools"]
    assert len(sent_tools) == 1, "empty tools list must collapse to forced submit_output mode"
    assert sent_tools[0]["name"] == "submit_output"
    assert fake.last_kwargs["tool_choice"] == {"type": "tool", "name": "submit_output"}, \
        "tool_choice must force submit_output when no real tools are declared"
    assert json.loads(resp.content) == {"summary": "ok", "score": 1.0}
    assert resp.raw["submit_output_fired"] is True


# ── Coexist mode (schema + tools) — H1 core fix ────────────────────────────


@pytest.mark.asyncio
async def test_coexist_mode_injects_submit_output_alongside_tools() -> None:
    fake = _FakeClient([
        _FakeContentBlock(tool_use={
            "id": "tu_1", "name": "file_read",
            "input": {"path": "README.md"},
        }),
    ])
    prov = _provider_with_fake(fake)

    agent_tools = [{"name": "file_read", "description": "Read a file", "input_schema": {}}]
    resp = await prov.chat(
        [{"role": "user", "content": "read the readme"}],
        tools=agent_tools,
        output_schema=OUTPUT_SCHEMA,
    )

    # Both tools should be present; no forced tool_choice.
    sent_tools = fake.last_kwargs["tools"]
    assert len(sent_tools) == 2
    names = {t["name"] for t in sent_tools}
    assert names == {"file_read", "submit_output"}
    assert "tool_choice" not in fake.last_kwargs

    # LLM called file_read (not submit_output) → normal tool_call returned.
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0]["name"] == "file_read"
    assert resp.raw["submit_output_fired"] is False


@pytest.mark.asyncio
async def test_coexist_mode_llm_calls_submit_output() -> None:
    fake = _FakeClient([
        _FakeContentBlock(text="Done."),
        _FakeContentBlock(tool_use={
            "id": "tu_final", "name": "submit_output",
            "input": {"summary": "finished the task", "score": 0.95},
        }),
    ])
    prov = _provider_with_fake(fake)

    agent_tools = [{"name": "file_read", "description": "Read a file", "input_schema": {}}]
    resp = await prov.chat(
        [{"role": "user", "content": "do the thing"}],
        tools=agent_tools,
        output_schema=OUTPUT_SCHEMA,
    )

    # submit_output fired → content = JSON, tool_calls empty so strategy sees completion.
    parsed = json.loads(resp.content)
    assert parsed == {"summary": "finished the task", "score": 0.95}
    assert resp.tool_calls == []
    assert resp.raw["submit_output_fired"] is True


@pytest.mark.asyncio
async def test_coexist_mode_submit_output_suppresses_sibling_tool_calls() -> None:
    """If LLM calls both submit_output AND another tool in one turn, the
    completion wins — sibling tool_calls are dropped to keep the signal clean."""
    fake = _FakeClient([
        _FakeContentBlock(tool_use={
            "id": "tu_A", "name": "file_read", "input": {"path": "a.md"},
        }),
        _FakeContentBlock(tool_use={
            "id": "tu_B", "name": "submit_output",
            "input": {"summary": "done"},
        }),
    ])
    prov = _provider_with_fake(fake)

    resp = await prov.chat(
        [{"role": "user", "content": "x"}],
        tools=[{"name": "file_read", "description": "", "input_schema": {}}],
        output_schema=OUTPUT_SCHEMA,
    )
    assert resp.tool_calls == []
    assert json.loads(resp.content) == {"summary": "done"}
    assert resp.raw["submit_output_fired"] is True


# ── No-schema path (unchanged parity) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_no_schema_no_submit_output_injected() -> None:
    fake = _FakeClient([_FakeContentBlock(text="plain answer")])
    prov = _provider_with_fake(fake)

    agent_tools = [{"name": "file_read", "description": "", "input_schema": {}}]
    resp = await prov.chat([{"role": "user", "content": "x"}], tools=agent_tools)

    # Tools unchanged; no submit_output added.
    sent_tools = fake.last_kwargs["tools"]
    assert sent_tools == agent_tools
    assert resp.content == "plain answer"
    assert resp.raw["submit_output_fired"] is False


@pytest.mark.asyncio
async def test_no_schema_no_tools_no_submit_output() -> None:
    fake = _FakeClient([_FakeContentBlock(text="plain")])
    prov = _provider_with_fake(fake)

    resp = await prov.chat([{"role": "user", "content": "x"}])
    # No tools key should be passed.
    assert "tools" not in fake.last_kwargs
    assert "tool_choice" not in fake.last_kwargs
    assert resp.content == "plain"


# ── React integration: mode passes output_schema on every call ─────────────


class _StubProvider:
    """Records each chat call's kwargs; returns scripted responses in order."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        self.calls.append({"kwargs": dict(kwargs), "messages_count": len(messages)})
        resp = self._responses.pop(0)
        # Return dict-like (matches _resp_get fallback)
        return resp


@pytest.mark.asyncio
async def test_react_passes_output_schema_on_main_call() -> None:
    from harness.core.modes.react import ReActStrategy

    # LLM returns no tool calls + JSON content → react exits on first step.
    provider = _StubProvider([
        {"content": '{"summary": "done"}', "tool_calls": [], "tokens_used": 10},
    ])

    strategy = ReActStrategy(max_steps=3)
    result = await strategy.execute(
        messages=[{"role": "user", "content": "x"}],
        provider=provider,
        tool_executor=None,
        output_schema=OUTPUT_SCHEMA,
        declared_tools=[],
    )

    # At least one main-loop call must have carried output_schema so the
    # provider's coexist mode kicks in.
    schemas_seen = [c["kwargs"].get("output_schema") for c in provider.calls]
    assert OUTPUT_SCHEMA in schemas_seen
    assert result["content"] == '{"summary": "done"}'


@pytest.mark.asyncio
async def test_react_no_schema_no_schema_kwarg_sent() -> None:
    """Parity: when no output_schema is set, react must NOT add the kwarg."""
    from harness.core.modes.react import ReActStrategy

    provider = _StubProvider([
        {"content": "plain answer", "tool_calls": [], "tokens_used": 5},
    ])

    strategy = ReActStrategy(max_steps=3)
    await strategy.execute(
        messages=[{"role": "user", "content": "x"}],
        provider=provider,
        tool_executor=None,
        declared_tools=[],
    )
    for call in provider.calls:
        assert "output_schema" not in call["kwargs"]
