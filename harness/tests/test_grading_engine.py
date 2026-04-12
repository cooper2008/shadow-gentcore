"""Tests for GradingEngine."""

from __future__ import annotations

import pytest

from agent_contracts.contracts.feature_contract import (
    CriterionResult,
    CriterionStatus,
    FeatureContract,
)
from harness.core.grading_engine import GradingEngine


class MockProvider:
    """Mock LLM provider for llm_judge tests."""

    def __init__(self, response_content: str = "PASS looks good") -> None:
        self._response = response_content

    async def chat(self, messages, **kwargs):
        return {"content": self._response, "tokens_used": 100}


class TestGradingEngine:
    @pytest.mark.asyncio
    async def test_automated_check_pass(self) -> None:
        engine = GradingEngine()
        engine.register_check("compiles", lambda out: (True, "Compilation succeeded"))

        contract = FeatureContract(contract_id="fc-1", criteria=["compiles"])
        graded = await engine.grade(contract, {"code": "print('hi')"})

        assert len(graded.results) == 1
        assert graded.results[0].status == CriterionStatus.PASS
        assert graded.overall_pass() is True

    @pytest.mark.asyncio
    async def test_automated_check_fail(self) -> None:
        engine = GradingEngine()
        engine.register_check("compiles", lambda out: (False, "Syntax error on line 5"))

        contract = FeatureContract(contract_id="fc-1", criteria=["compiles"])
        graded = await engine.grade(contract, {"code": "bad code"})

        assert graded.results[0].status == CriterionStatus.FAIL
        assert "Syntax error" in graded.results[0].reason
        assert graded.overall_pass() is False

    @pytest.mark.asyncio
    async def test_automated_check_exception(self) -> None:
        def bad_check(output):
            raise ValueError("check crashed")

        engine = GradingEngine()
        engine.register_check("crashes", bad_check)

        contract = FeatureContract(contract_id="fc-1", criteria=["crashes"])
        graded = await engine.grade(contract, {})

        assert graded.results[0].status == CriterionStatus.FAIL
        assert "exception" in graded.results[0].reason.lower()

    @pytest.mark.asyncio
    async def test_llm_judge_pass(self) -> None:
        engine = GradingEngine()
        provider = MockProvider("PASS - Code is clean and well-structured")

        contract = FeatureContract(contract_id="fc-1", criteria=["code is clean"])
        graded = await engine.grade(contract, {"code": "clean code"}, provider=provider)

        assert graded.results[0].status == CriterionStatus.PASS

    @pytest.mark.asyncio
    async def test_llm_judge_fail(self) -> None:
        engine = GradingEngine()
        provider = MockProvider("FAIL - Code has major issues")

        contract = FeatureContract(contract_id="fc-1", criteria=["code is clean"])
        graded = await engine.grade(contract, {"code": "bad"}, provider=provider)

        assert graded.results[0].status == CriterionStatus.FAIL

    @pytest.mark.asyncio
    async def test_skip_no_check_no_provider(self) -> None:
        engine = GradingEngine()
        contract = FeatureContract(contract_id="fc-1", criteria=["unregistered criterion"])
        graded = await engine.grade(contract, {})

        assert graded.results[0].status == CriterionStatus.SKIP

    @pytest.mark.asyncio
    async def test_mixed_criteria(self) -> None:
        engine = GradingEngine()
        engine.register_check("compiles", lambda out: (True, None))
        engine.register_check("tests_pass", lambda out: (False, "2 failures"))

        contract = FeatureContract(
            contract_id="fc-1",
            criteria=["compiles", "tests_pass"],
        )
        graded = await engine.grade(contract, {})

        assert graded.results[0].status == CriterionStatus.PASS
        assert graded.results[1].status == CriterionStatus.FAIL
        assert graded.overall_pass() is False

    @pytest.mark.asyncio
    async def test_score_calculation(self) -> None:
        engine = GradingEngine()
        engine.register_check("a", lambda out: (True, None))
        engine.register_check("b", lambda out: (False, None))

        contract = FeatureContract(contract_id="fc-1", criteria=["a", "b"])
        graded = await engine.grade(contract, {})

        assert graded.score() == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_original_contract_unchanged(self) -> None:
        engine = GradingEngine()
        engine.register_check("c", lambda out: (True, None))

        contract = FeatureContract(contract_id="fc-1", criteria=["c"])
        graded = await engine.grade(contract, {})

        assert len(contract.results) == 0  # original unchanged
        assert len(graded.results) == 1
