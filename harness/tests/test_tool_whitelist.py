"""Tests for manifest-driven tool whitelist (Phase 1 progressive disclosure).

Verifies that:
- _build_anthropic_tools(allowed=[...]) returns only the declared tools
- AgentRunner extracts declared_tools from dict and Pydantic manifests
- ReActStrategy only presents whitelisted tools to the LLM (captured in chat kwargs)
- Agents with no tool declarations still work (backward compat — no filtering)
- Adapters that are registered but not declared are invisible to the LLM
"""

from __future__ import annotations

from typing import Any

import pytest

from harness.core.modes.react import ReActStrategy, _build_anthropic_tools
from harness.core.agent_runner import AgentRunner


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


class _MockToolExecutor:
    """Executor with a fixed set of 'registered' adapters."""

    def __init__(self, registered: list[str]) -> None:
        self._adapters: dict[str, Any] = {name: object() for name in registered}

    async def execute(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        return {"output": "ok", "success": True}


class _CapturingProvider:
    """Provider that records every chat() call's kwargs for inspection."""

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self._responses = list(responses or [
            {"content": "done", "tokens_used": 10, "tool_calls": []},
        ])
        self._cursor = 0
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if self._cursor >= len(self._responses):
            return {"content": "done", "tokens_used": 0, "tool_calls": []}
        resp = self._responses[self._cursor]
        self._cursor += 1
        return resp


# ---------------------------------------------------------------------------
# _build_anthropic_tools whitelist unit tests
# ---------------------------------------------------------------------------


class TestBuildAnthropicToolsWhitelist:
    def _make_executor(self, names: list[str]) -> _MockToolExecutor:
        return _MockToolExecutor(names)

    def test_no_allowed_returns_all_adapters(self) -> None:
        executor = self._make_executor(["file_read", "shell_exec", "list_dir"])
        tools = _build_anthropic_tools(executor, allowed=None)
        names = {t["name"] for t in tools}
        assert names == {"file_read", "shell_exec", "list_dir"}

    def test_empty_allowed_returns_all_adapters(self) -> None:
        executor = self._make_executor(["file_read", "shell_exec"])
        tools = _build_anthropic_tools(executor, allowed=[])
        # Empty list treated as "no filter" → backward compat
        names = {t["name"] for t in tools}
        assert names == {"file_read", "shell_exec"}

    def test_allowed_filters_to_declared_subset(self) -> None:
        # Executor has 5 tools registered; agent only declares 2
        executor = self._make_executor(
            ["file_read", "file_write", "shell_exec", "search_code", "list_dir"]
        )
        tools = _build_anthropic_tools(executor, allowed=["file_read", "search_code"])
        names = {t["name"] for t in tools}
        assert names == {"file_read", "search_code"}
        assert "file_write" not in names
        assert "shell_exec" not in names
        assert "list_dir" not in names

    def test_allowed_tool_not_in_executor_is_ignored(self) -> None:
        # Manifest declares a tool that isn't registered yet (e.g. MCP not loaded)
        executor = self._make_executor(["file_read"])
        tools = _build_anthropic_tools(executor, allowed=["file_read", "ghost_tool"])
        names = {t["name"] for t in tools}
        assert names == {"file_read"}
        assert "ghost_tool" not in names

    def test_all_declared_not_registered_returns_empty(self) -> None:
        executor = self._make_executor(["file_read"])
        tools = _build_anthropic_tools(executor, allowed=["nonexistent"])
        assert tools == []

    def test_known_schema_tools_have_full_input_schema(self) -> None:
        executor = self._make_executor(["file_read", "search_code"])
        tools = _build_anthropic_tools(executor, allowed=["file_read"])
        assert len(tools) == 1
        tool = tools[0]
        assert tool["name"] == "file_read"
        assert "input_schema" in tool
        assert "properties" in tool["input_schema"]
        assert "path" in tool["input_schema"]["properties"]

    def test_unknown_tool_gets_generic_schema(self) -> None:
        executor = self._make_executor(["custom_deploy"])
        tools = _build_anthropic_tools(executor, allowed=["custom_deploy"])
        assert len(tools) == 1
        assert tools[0]["name"] == "custom_deploy"
        assert "input_schema" in tools[0]

    def test_none_executor_returns_empty(self) -> None:
        tools = _build_anthropic_tools(None, allowed=["file_read"])
        assert tools == []


# ---------------------------------------------------------------------------
# Token savings verification
# ---------------------------------------------------------------------------


class TestTokenSavings:
    def test_whitelist_dramatically_reduces_tool_count(self) -> None:
        """Simulates a large executor (many registered adapters) vs small manifest."""
        all_tools = [f"tool_{i}" for i in range(129)]  # simulates full registry
        executor = _MockToolExecutor(all_tools)

        # Without whitelist — all 129 tools sent to LLM
        full = _build_anthropic_tools(executor, allowed=None)
        assert len(full) == 129

        # With manifest whitelist — only 4 declared tools
        declared = ["tool_0", "tool_5", "tool_42", "tool_100"]
        filtered = _build_anthropic_tools(executor, allowed=declared)
        assert len(filtered) == 4

        reduction = 1 - len(filtered) / len(full)
        assert reduction > 0.96  # >96% reduction


# ---------------------------------------------------------------------------
# ReActStrategy integration — whitelist flows to LLM
# ---------------------------------------------------------------------------


class TestReActWhitelistIntegration:
    @pytest.mark.asyncio
    async def test_declared_tools_sent_to_provider(self) -> None:
        """Only declared tools appear in the 'tools' kwarg sent to provider.chat()."""
        executor = _MockToolExecutor(["file_read", "shell_exec", "search_code", "list_dir"])
        provider = _CapturingProvider([
            {"content": "done", "tokens_used": 5, "tool_calls": []},
        ])
        strategy = ReActStrategy(max_steps=3)

        await strategy.execute(
            messages=[{"role": "user", "content": "do task"}],
            provider=provider,
            tool_executor=executor,
            declared_tools=["file_read", "search_code"],  # only 2 of 4
        )

        # First chat call should have exactly the 2 declared tools
        first_call_tools = provider.calls[0]["kwargs"].get("tools", [])
        tool_names = {t["name"] for t in first_call_tools}
        assert tool_names == {"file_read", "search_code"}
        assert "shell_exec" not in tool_names
        assert "list_dir" not in tool_names

    @pytest.mark.asyncio
    async def test_no_declared_tools_passes_all_registered(self) -> None:
        """Backward compat: no declared_tools kwarg → all registered tools passed."""
        executor = _MockToolExecutor(["file_read", "shell_exec"])
        provider = _CapturingProvider([
            {"content": "done", "tokens_used": 5, "tool_calls": []},
        ])
        strategy = ReActStrategy(max_steps=3)

        await strategy.execute(
            messages=[{"role": "user", "content": "do task"}],
            provider=provider,
            tool_executor=executor,
            # no declared_tools
        )

        first_call_tools = provider.calls[0]["kwargs"].get("tools", [])
        tool_names = {t["name"] for t in first_call_tools}
        assert tool_names == {"file_read", "shell_exec"}

    @pytest.mark.asyncio
    async def test_empty_declared_tools_passes_all_registered(self) -> None:
        """Empty declared_tools list → all registered tools (backward compat)."""
        executor = _MockToolExecutor(["file_read", "shell_exec"])
        provider = _CapturingProvider([
            {"content": "done", "tokens_used": 5, "tool_calls": []},
        ])
        strategy = ReActStrategy(max_steps=3)

        await strategy.execute(
            messages=[{"role": "user", "content": "do task"}],
            provider=provider,
            tool_executor=executor,
            declared_tools=[],
        )

        first_call_tools = provider.calls[0]["kwargs"].get("tools", [])
        tool_names = {t["name"] for t in first_call_tools}
        assert tool_names == {"file_read", "shell_exec"}


# ---------------------------------------------------------------------------
# AgentRunner — declared_tools extracted from manifest dict
# ---------------------------------------------------------------------------


class TestAgentRunnerDeclaredTools:
    def _make_runner(self, provider: Any) -> AgentRunner:
        return AgentRunner(provider=provider)

    def _make_manifest(self, tools: list[Any]) -> dict[str, Any]:
        return {
            "id": "test/Agent/v1",
            "domain": "test",
            "pack": "core",
            "category": "fast-codegen",
            "system_prompt_ref": "prompt.md",
            "tools": tools,
        }

    @pytest.mark.asyncio
    async def test_dict_tool_entries_extracted(self) -> None:
        """Tools as list of dicts: [{name: ..., desc: ...}]."""
        provider = _CapturingProvider()
        executor = _MockToolExecutor(["file_read", "search_code", "shell_exec"])
        runner = AgentRunner(provider=provider, tool_executor=executor)

        manifest = self._make_manifest([
            {"name": "file_read", "desc": "read files"},
            {"name": "search_code", "desc": "grep"},
        ])

        await runner.run(manifest=manifest, task={}, system_prompt_content="You are an agent.")

        # Check tools sent to provider on first call
        first_call = provider.calls[0]
        tool_names = {t["name"] for t in first_call["kwargs"].get("tools", [])}
        assert tool_names == {"file_read", "search_code"}
        assert "shell_exec" not in tool_names

    @pytest.mark.asyncio
    async def test_string_tool_entries_extracted(self) -> None:
        """Tools as list of strings: ['file_read', 'search_code']."""
        provider = _CapturingProvider()
        executor = _MockToolExecutor(["file_read", "search_code", "shell_exec"])
        runner = AgentRunner(provider=provider, tool_executor=executor)

        manifest = self._make_manifest(["file_read", "search_code"])

        await runner.run(manifest=manifest, task={}, system_prompt_content="You are an agent.")

        first_call = provider.calls[0]
        tool_names = {t["name"] for t in first_call["kwargs"].get("tools", [])}
        assert tool_names == {"file_read", "search_code"}

    @pytest.mark.asyncio
    async def test_no_tools_in_manifest_passes_all(self) -> None:
        """No tools key in manifest → no filter → all registered tools passed."""
        provider = _CapturingProvider()
        executor = _MockToolExecutor(["file_read", "shell_exec"])
        runner = AgentRunner(provider=provider, tool_executor=executor)

        manifest = {
            "id": "test/Agent/v1",
            "domain": "test",
            "pack": "core",
            "category": "fast-codegen",
            "system_prompt_ref": "prompt.md",
            # no 'tools' key
        }

        await runner.run(manifest=manifest, task={}, system_prompt_content="You are an agent.")

        first_call = provider.calls[0]
        tool_names = {t["name"] for t in first_call["kwargs"].get("tools", [])}
        assert tool_names == {"file_read", "shell_exec"}
