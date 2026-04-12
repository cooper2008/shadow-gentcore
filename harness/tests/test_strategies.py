"""Tests for execution strategies (ReAct, PlanExecute, ChainOfThought)."""

from __future__ import annotations

from typing import Any

import pytest

from harness.core.modes.react import ReActStrategy
from harness.core.modes.plan_execute import PlanExecuteStrategy
from harness.core.modes.chain_of_thought import ChainOfThoughtStrategy


class MockProvider:
    """Mock provider that returns pre-configured responses."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self._cursor = 0

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        if self._cursor >= len(self._responses):
            return {"content": "done", "tokens_used": 10, "tool_calls": []}
        resp = self._responses[self._cursor]
        self._cursor += 1
        return resp


class MockToolExecutor:
    """Mock tool executor that returns pre-configured results."""

    def __init__(self, results: dict[str, str] | None = None) -> None:
        self._results = results or {}

    async def execute(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        name = tool_call.get("name", "")
        return {"output": self._results.get(name, f"result of {name}"), "success": True}


class TestReActStrategy:
    @pytest.mark.asyncio
    async def test_single_pass_no_tools(self) -> None:
        provider = MockProvider([
            {"content": "Final answer", "tokens_used": 50, "tool_calls": []},
        ])
        strategy = ReActStrategy(max_steps=5)
        result = await strategy.execute(
            messages=[{"role": "user", "content": "hello"}],
            provider=provider,
        )
        assert result["content"] == "Final answer"
        assert result["tokens_used"] == 50
        assert len(result["steps"]) == 1

    @pytest.mark.asyncio
    async def test_tool_call_then_answer(self) -> None:
        provider = MockProvider([
            {"content": "Let me check", "tokens_used": 30, "tool_calls": [
                {"id": "tc-1", "name": "pytest", "arguments": {}}
            ]},
            {"content": "All tests pass", "tokens_used": 20, "tool_calls": []},
        ])
        tool_executor = MockToolExecutor({"pytest": "5 passed"})
        strategy = ReActStrategy(max_steps=5)
        result = await strategy.execute(
            messages=[{"role": "user", "content": "run tests"}],
            provider=provider,
            tool_executor=tool_executor,
        )
        assert result["content"] == "All tests pass"
        assert result["tokens_used"] == 50
        assert len(result["steps"]) >= 2

    @pytest.mark.asyncio
    async def test_max_steps_truncation(self) -> None:
        # Provider always returns tool calls, never a final answer
        provider = MockProvider([
            {"content": f"step {i}", "tokens_used": 10, "tool_calls": [
                {"id": f"tc-{i}", "name": "tool", "arguments": {}}
            ]}
            for i in range(10)
        ])
        strategy = ReActStrategy(max_steps=3)
        result = await strategy.execute(
            messages=[{"role": "user", "content": "do stuff"}],
            provider=provider,
            tool_executor=MockToolExecutor(),
        )
        assert result.get("truncated") is True

    @pytest.mark.asyncio
    async def test_name_property(self) -> None:
        assert ReActStrategy().name == "react"


class TestPlanExecuteStrategy:
    @pytest.mark.asyncio
    async def test_plan_then_execute(self) -> None:
        provider = MockProvider([
            {"content": "1. Do A\n2. Do B", "tokens_used": 30, "tool_calls": []},
            {"content": "Step 1 done. Task complete.", "tokens_used": 20, "tool_calls": []},
        ])
        strategy = PlanExecuteStrategy(max_plan_steps=3)
        result = await strategy.execute(
            messages=[{"role": "user", "content": "build feature"}],
            provider=provider,
        )
        assert result["tokens_used"] == 50
        assert any(s["type"] == "plan" for s in result["steps"])
        assert any(s["type"] == "execute" for s in result["steps"])

    @pytest.mark.asyncio
    async def test_name_property(self) -> None:
        assert PlanExecuteStrategy().name == "plan_execute"


class TestChainOfThoughtStrategy:
    @pytest.mark.asyncio
    async def test_single_pass(self) -> None:
        provider = MockProvider([
            {"content": "Deep analysis result", "tokens_used": 100, "tool_calls": []},
        ])
        strategy = ChainOfThoughtStrategy()
        result = await strategy.execute(
            messages=[{"role": "user", "content": "analyze this"}],
            provider=provider,
        )
        assert result["content"] == "Deep analysis result"
        assert result["tokens_used"] == 100
        assert len(result["steps"]) == 1
        assert result["steps"][0]["type"] == "reason"

    @pytest.mark.asyncio
    async def test_name_property(self) -> None:
        assert ChainOfThoughtStrategy().name == "chain_of_thought"
