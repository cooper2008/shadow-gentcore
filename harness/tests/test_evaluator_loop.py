"""Tests for EvaluatorLoop."""

from __future__ import annotations

from typing import Any

import pytest

from harness.core.evaluator_loop import EvaluatorLoop


class TestEvaluatorLoop:
    @pytest.mark.asyncio
    async def test_pass_first_round(self) -> None:
        """Generator passes on first attempt."""

        async def planner(ctx: dict[str, Any]) -> dict[str, Any]:
            return {"contract_id": "c-1", "criteria": ["criterion_a", "criterion_b"]}

        async def generator(ctx: dict[str, Any]) -> dict[str, Any]:
            return {"output": "good code"}

        async def evaluator(ctx: dict[str, Any]) -> dict[str, Any]:
            return {"overall_pass": True, "score": 1.0, "feedback": ""}

        loop = EvaluatorLoop(planner_fn=planner, generator_fn=generator, evaluator_fn=evaluator)
        result = await loop.run({"task": "build feature"})

        assert result["status"] == "passed"
        assert result["rounds"] == 1
        assert result["contract"]["contract_id"] == "c-1"

    @pytest.mark.asyncio
    async def test_fail_then_pass(self) -> None:
        """Generator fails first round, passes second."""
        call_count = {"gen": 0, "eval": 0}

        async def planner(ctx: dict[str, Any]) -> dict[str, Any]:
            return {"contract_id": "c-2", "criteria": ["test_passes"]}

        async def generator(ctx: dict[str, Any]) -> dict[str, Any]:
            call_count["gen"] += 1
            return {"output": f"attempt_{call_count['gen']}"}

        async def evaluator(ctx: dict[str, Any]) -> dict[str, Any]:
            call_count["eval"] += 1
            if call_count["eval"] == 1:
                return {"overall_pass": False, "score": 0.5, "feedback": "Fix the tests"}
            return {"overall_pass": True, "score": 1.0, "feedback": ""}

        loop = EvaluatorLoop(
            planner_fn=planner, generator_fn=generator, evaluator_fn=evaluator, max_rounds=3,
        )
        result = await loop.run({"task": "fix bug"})

        assert result["status"] == "passed"
        assert result["rounds"] == 2
        assert call_count["gen"] == 2
        assert call_count["eval"] == 2

    @pytest.mark.asyncio
    async def test_exhaust_all_rounds(self) -> None:
        """Generator fails all rounds."""

        async def planner(ctx: dict[str, Any]) -> dict[str, Any]:
            return {"contract_id": "c-3", "criteria": ["impossible"]}

        async def generator(ctx: dict[str, Any]) -> dict[str, Any]:
            return {"output": "bad"}

        async def evaluator(ctx: dict[str, Any]) -> dict[str, Any]:
            return {"overall_pass": False, "score": 0.0, "feedback": "Still failing"}

        loop = EvaluatorLoop(
            planner_fn=planner, generator_fn=generator, evaluator_fn=evaluator, max_rounds=2,
        )
        result = await loop.run({"task": "impossible task"})

        assert result["status"] == "failed"
        assert result["rounds"] == 2

    @pytest.mark.asyncio
    async def test_threshold_based_pass(self) -> None:
        """Pass based on score threshold rather than overall_pass."""

        async def planner(ctx: dict[str, Any]) -> dict[str, Any]:
            return {"contract_id": "c-4", "criteria": ["a", "b", "c"]}

        async def generator(ctx: dict[str, Any]) -> dict[str, Any]:
            return {"output": "partial"}

        async def evaluator(ctx: dict[str, Any]) -> dict[str, Any]:
            return {"overall_pass": False, "score": 0.8, "feedback": "Close enough"}

        loop = EvaluatorLoop(
            planner_fn=planner, generator_fn=generator, evaluator_fn=evaluator,
            max_rounds=3, threshold=0.8,
        )
        result = await loop.run({"task": "soft pass"})

        assert result["status"] == "passed"
        assert result["rounds"] == 1

    @pytest.mark.asyncio
    async def test_history_tracked(self) -> None:
        """History captures plan, generate, evaluate phases."""

        async def planner(ctx: dict[str, Any]) -> dict[str, Any]:
            return {"criteria": ["x"]}

        async def generator(ctx: dict[str, Any]) -> dict[str, Any]:
            return {"output": "y"}

        async def evaluator(ctx: dict[str, Any]) -> dict[str, Any]:
            return {"overall_pass": True, "score": 1.0}

        loop = EvaluatorLoop(planner_fn=planner, generator_fn=generator, evaluator_fn=evaluator)
        await loop.run({})

        phases = [h["phase"] for h in loop.history]
        assert phases == ["plan", "generate", "evaluate"]

    @pytest.mark.asyncio
    async def test_feedback_passed_to_generator(self) -> None:
        """Feedback from evaluator is passed to generator on retry."""
        received_feedback: list[str | None] = []

        async def planner(ctx: dict[str, Any]) -> dict[str, Any]:
            return {"criteria": ["check"]}

        async def generator(ctx: dict[str, Any]) -> dict[str, Any]:
            received_feedback.append(ctx.get("feedback"))
            return {"output": "attempt"}

        call_count = {"n": 0}

        async def evaluator(ctx: dict[str, Any]) -> dict[str, Any]:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"overall_pass": False, "score": 0.0, "feedback": "Add error handling"}
            return {"overall_pass": True, "score": 1.0}

        loop = EvaluatorLoop(
            planner_fn=planner, generator_fn=generator, evaluator_fn=evaluator, max_rounds=3,
        )
        await loop.run({})

        assert received_feedback[0] is None  # first round, no feedback
        assert received_feedback[1] == "Add error handling"  # second round
