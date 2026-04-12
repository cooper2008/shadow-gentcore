"""Tests for AgentRunner reflexion/self-critique support."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent_contracts.manifests.agent_manifest import AgentManifest
from agent_contracts.contracts.task_envelope import TaskEnvelope
from agent_contracts.contracts.feature_contract import (
    CriterionResult,
    CriterionStatus,
    FeatureContract,
)

from harness.core.agent_runner import AgentRunner
from harness.core.grading_engine import GradingEngine


def _make_manifest() -> AgentManifest:
    return AgentManifest(
        id="test/ReflexAgent/v1",
        domain="test",
        category="reasoning",
        version="1.0.0",
        system_prompt_ref="system_prompt.md",
    )


def _make_task() -> TaskEnvelope:
    return TaskEnvelope(
        task_id="t-reflex-1",
        agent_id="test/ReflexAgent/v1",
        input_payload={"request": "write code"},
        budget_tokens=10000,
    )


def _make_provider() -> AsyncMock:
    provider = AsyncMock()
    provider.chat = AsyncMock(return_value={"content": "generated code", "tokens_used": 50})
    return provider


class TestAgentRunnerReflexion:
    @pytest.mark.asyncio
    async def test_reflexion_no_grading_engine(self) -> None:
        """Without grading engine, reflexion runs once and returns."""
        provider = _make_provider()
        runner = AgentRunner(provider=provider)

        result = await runner.run_with_reflexion(
            manifest=_make_manifest(),
            task=_make_task(),
            system_prompt_content="You are a coder.",
        )

        assert result["rounds"] == 1
        assert result["reflexion_history"][0]["graded"] is False
        assert result["reflexion_history"][0]["action"] == "no_grading"

    @pytest.mark.asyncio
    async def test_reflexion_pass_first_round(self) -> None:
        """Grading passes on first round — no retry needed."""
        provider = _make_provider()
        grading_engine = GradingEngine()
        grading_engine.register_check(
            "has_output", lambda output: (True, "Output present")
        )

        contract = FeatureContract(
            contract_id="c-1",
            criteria=["has_output"],
        )

        runner = AgentRunner(provider=provider, grading_engine=grading_engine)
        result = await runner.run_with_reflexion(
            manifest=_make_manifest(),
            task=_make_task(),
            system_prompt_content="You are a coder.",
            contract=contract,
        )

        assert result["rounds"] == 1
        assert result["reflexion_history"][0]["graded"] is True
        assert result["reflexion_history"][0]["action"] == "pass"

    @pytest.mark.asyncio
    async def test_reflexion_fail_then_pass(self) -> None:
        """Grading fails first round, passes second — critique injected."""
        provider = _make_provider()
        call_count = {"n": 0}

        grading_engine = GradingEngine()

        def check_fn(output: dict[str, Any]) -> tuple[bool, str]:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return (False, "Missing docstrings")
            return (True, "Docstrings present")

        grading_engine.register_check("has_docstrings", check_fn)

        contract = FeatureContract(
            contract_id="c-2",
            criteria=["has_docstrings"],
        )

        runner = AgentRunner(provider=provider, grading_engine=grading_engine)
        result = await runner.run_with_reflexion(
            manifest=_make_manifest(),
            task=_make_task(),
            system_prompt_content="You are a coder.",
            contract=contract,
            max_reflexion_rounds=3,
        )

        assert result["rounds"] == 2
        history = result["reflexion_history"]
        assert history[0]["action"] == "retry_with_critique"
        assert "critique" in history[0]
        assert "Missing docstrings" in history[0]["critique"]
        assert history[1]["action"] == "pass"

    @pytest.mark.asyncio
    async def test_reflexion_exhausts_rounds(self) -> None:
        """All rounds fail — returns last result."""
        provider = _make_provider()
        grading_engine = GradingEngine()
        grading_engine.register_check(
            "impossible", lambda output: (False, "Always fails")
        )

        contract = FeatureContract(
            contract_id="c-3",
            criteria=["impossible"],
        )

        runner = AgentRunner(provider=provider, grading_engine=grading_engine)
        result = await runner.run_with_reflexion(
            manifest=_make_manifest(),
            task=_make_task(),
            system_prompt_content="You are a coder.",
            contract=contract,
            max_reflexion_rounds=2,
        )

        assert result["rounds"] == 2
        for entry in result["reflexion_history"]:
            assert entry["action"] == "retry_with_critique"

    @pytest.mark.asyncio
    async def test_build_critique_format(self) -> None:
        """_build_critique produces readable critique text."""
        graded = FeatureContract(
            contract_id="c-4",
            criteria=["criterion_a", "criterion_b"],
            results=[
                CriterionResult(name="criterion_a", status=CriterionStatus.PASS, reason="ok"),
                CriterionResult(name="criterion_b", status=CriterionStatus.FAIL, reason="missing"),
            ],
        )
        critique = AgentRunner._build_critique(graded)
        assert "criterion_b" in critique
        assert "fail" in critique.lower()
        assert "missing" in critique
        assert "criterion_b" in critique
