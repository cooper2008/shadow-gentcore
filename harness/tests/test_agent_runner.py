"""Tests for AgentRunner — full execution pipeline."""

from __future__ import annotations

from typing import Any

import pytest

from agent_contracts.manifests.agent_manifest import AgentManifest
from agent_contracts.contracts.task_envelope import TaskEnvelope
from agent_contracts.contracts.run_record import RunStatus
from harness.core.agent_runner import AgentRunner


class MockProvider:
    """Mock LLM provider."""

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self._responses = responses or [
            {"content": "Done", "tokens_used": 100, "tool_calls": []},
        ]
        self._cursor = 0

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        if self._cursor >= len(self._responses):
            return {"content": "exhausted", "tokens_used": 10, "tool_calls": []}
        resp = self._responses[self._cursor]
        self._cursor += 1
        return resp


def make_manifest(**overrides: Any) -> AgentManifest:
    defaults = dict(
        id="backend/TestAgent/v1",
        domain="backend",
        pack="core",
        category="fast-codegen",
        system_prompt_ref="prompt.md",
    )
    defaults.update(overrides)
    return AgentManifest(**defaults)


def make_task(**overrides: Any) -> TaskEnvelope:
    defaults = dict(
        task_id="task-1",
        agent_id="backend/TestAgent/v1",
        input_payload={"instruction": "Do something"},
    )
    defaults.update(overrides)
    return TaskEnvelope(**defaults)


class TestAgentRunner:
    @pytest.mark.asyncio
    async def test_basic_run(self) -> None:
        provider = MockProvider()
        runner = AgentRunner(provider=provider)
        manifest = make_manifest()
        task = make_task()

        output = await runner.run(
            manifest=manifest,
            task=task,
            system_prompt_content="You are a test agent.",
        )

        assert output["run_record"].status == RunStatus.SUCCESS
        assert output["result"]["content"] == "Done"
        assert output["budget_summary"]["tokens_used"] == 100

    @pytest.mark.asyncio
    async def test_run_with_budget_limit(self) -> None:
        provider = MockProvider([
            {"content": "big response", "tokens_used": 5000, "tool_calls": []},
        ])
        runner = AgentRunner(provider=provider)
        manifest = make_manifest()
        task = make_task(budget_tokens=1000)

        output = await runner.run(
            manifest=manifest,
            task=task,
            system_prompt_content="You are a test agent.",
        )

        assert output["run_record"].status == RunStatus.FAILURE
        assert "budget" in output["run_record"].failure_reason.lower()

    @pytest.mark.asyncio
    async def test_run_with_tools(self) -> None:
        provider = MockProvider([
            {"content": "Done with tools", "tokens_used": 80, "tool_calls": []},
        ])
        runner = AgentRunner(provider=provider)
        manifest = make_manifest()
        task = make_task()

        output = await runner.run(
            manifest=manifest,
            task=task,
            system_prompt_content="You are a test agent.",
            tool_descriptions=[{"name": "pytest", "description": "Run tests"}],
        )

        assert output["run_record"].status == RunStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_run_with_context(self) -> None:
        provider = MockProvider()
        runner = AgentRunner(provider=provider)
        manifest = make_manifest()
        task = make_task()

        output = await runner.run(
            manifest=manifest,
            task=task,
            system_prompt_content="You are a test agent.",
            context_items=[{"source": "repo_map", "content": "src/ has code"}],
        )

        assert output["run_record"].status == RunStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_run_record_has_trace_id(self) -> None:
        provider = MockProvider()
        runner = AgentRunner(provider=provider)
        manifest = make_manifest()
        task = make_task()

        output = await runner.run(
            manifest=manifest,
            task=task,
            system_prompt_content="Agent prompt.",
        )

        assert output["run_record"].trace_id.startswith("trace-task-1-")

    @pytest.mark.asyncio
    async def test_execution_mode_override_from_task(self) -> None:
        provider = MockProvider()
        runner = AgentRunner(provider=provider)
        manifest = make_manifest()
        task = make_task(execution_mode_override={"strategy": "chain_of_thought"})

        output = await runner.run(
            manifest=manifest,
            task=task,
            system_prompt_content="Agent prompt.",
        )

        assert output["run_record"].status == RunStatus.SUCCESS
