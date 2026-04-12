"""Tests for ToolExecutor."""

from __future__ import annotations

import pytest

from harness.core.tool_executor import ToolExecutor


class MockAdapter:
    """Mock tool adapter for testing."""

    def __init__(self, output: str = "mock output", should_fail: bool = False) -> None:
        self._output = output
        self._should_fail = should_fail

    async def invoke(self, tool_name: str, arguments: dict) -> str:
        if self._should_fail:
            raise RuntimeError("Tool execution failed")
        return self._output


class TestToolExecutor:
    @pytest.mark.asyncio
    async def test_execute_registered_tool(self) -> None:
        executor = ToolExecutor()
        executor.register_adapter("pytest", MockAdapter(output="All tests passed"))
        result = await executor.execute({"id": "tc-1", "name": "pytest", "arguments": {}})
        assert result["success"] is True
        assert result["output"] == "All tests passed"
        assert result["tool_name"] == "pytest"

    @pytest.mark.asyncio
    async def test_execute_unregistered_tool(self) -> None:
        executor = ToolExecutor()
        result = await executor.execute({"id": "tc-1", "name": "unknown_tool", "arguments": {}})
        assert result["success"] is False
        assert "No adapter registered" in result["output"]

    @pytest.mark.asyncio
    async def test_execute_failing_tool(self) -> None:
        executor = ToolExecutor()
        executor.register_adapter("bad_tool", MockAdapter(should_fail=True))
        result = await executor.execute({"id": "tc-1", "name": "bad_tool", "arguments": {}})
        assert result["success"] is False
        assert "Error" in result["output"]

    @pytest.mark.asyncio
    async def test_execution_log(self) -> None:
        executor = ToolExecutor()
        executor.register_adapter("tool_a", MockAdapter(output="a"))
        executor.register_adapter("tool_b", MockAdapter(output="b"))
        await executor.execute({"id": "1", "name": "tool_a", "arguments": {}})
        await executor.execute({"id": "2", "name": "tool_b", "arguments": {}})
        assert len(executor.execution_log) == 2
        assert executor.execution_log[0]["tool_name"] == "tool_a"
        assert executor.execution_log[1]["tool_name"] == "tool_b"

    @pytest.mark.asyncio
    async def test_clear_log(self) -> None:
        executor = ToolExecutor()
        executor.register_adapter("t", MockAdapter())
        await executor.execute({"id": "1", "name": "t", "arguments": {}})
        assert len(executor.execution_log) == 1
        executor.clear_log()
        assert len(executor.execution_log) == 0

    @pytest.mark.asyncio
    async def test_duration_tracked(self) -> None:
        executor = ToolExecutor()
        executor.register_adapter("t", MockAdapter())
        result = await executor.execute({"id": "1", "name": "t", "arguments": {}})
        assert "duration_ms" in result
        assert isinstance(result["duration_ms"], int)

    @pytest.mark.asyncio
    async def test_dict_output_normalized(self) -> None:
        class DictAdapter:
            async def invoke(self, name, args):
                return {"key": "value", "count": 42}

        executor = ToolExecutor()
        executor.register_adapter("dict_tool", DictAdapter())
        result = await executor.execute({"id": "1", "name": "dict_tool", "arguments": {}})
        assert result["success"] is True
        assert "key" in result["output"]
        assert "value" in result["output"]
