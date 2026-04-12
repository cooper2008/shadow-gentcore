"""Tests for CompositionEngine gate feedback injection and cross-stage feedback."""

from __future__ import annotations

from typing import Any

import pytest

from harness.core.composition_engine import CompositionEngine, GateFailure, HumanEscalation
from harness.core.feedback_loop import FeedbackLoop


class TestGateFeedbackInjection:
    """Task 68: gate retry with feedback injection."""

    @pytest.mark.asyncio
    async def test_gate_retry_injects_feedback(self) -> None:
        """On gate retry, feedback is injected into the step config."""
        engine = CompositionEngine()
        steps = [
            {
                "name": "step1",
                "agent": "a/B/v1",
                "gate": {
                    "name": "g1",
                    "condition": "always_fail",
                    "on_fail": "retry",
                    "max_retries": 1,
                },
            }
        ]
        result = await engine.execute(steps)
        log = result["execution_log"]

        retry_events = [e for e in log if e["event"] == "gate_retry"]
        assert len(retry_events) == 1
        assert retry_events[0]["strategy"] == "retry_with_feedback"

        fail_events = [e for e in log if e["event"] == "gate_failed"]
        assert len(fail_events) == 1
        assert "issues" in fail_events[0]

    @pytest.mark.asyncio
    async def test_gate_retry_passes_on_second(self) -> None:
        """Gate passes on retry — step result updated."""
        call_count = {"n": 0}

        class MockEngine(CompositionEngine):
            async def _execute_step(self, step_name, agent_id, config, dep_artifacts):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return {"step": step_name, "status": "failed"}
                return {"step": step_name, "status": "completed", "output": "ok"}

        engine = MockEngine()
        steps = [
            {
                "name": "codegen",
                "agent": "a/B/v1",
                "gate": {
                    "name": "g1",
                    "condition": "status == success",
                    "on_fail": "retry",
                    "max_retries": 2,
                },
            }
        ]
        result = await engine.execute(steps)
        assert result["status"] == "completed"
        log_events = [e["event"] for e in result["execution_log"]]
        assert "gate_passed_on_retry" in log_events

    @pytest.mark.asyncio
    async def test_gate_escalate_human(self) -> None:
        """escalate_human gate action raises HumanEscalation."""
        engine = CompositionEngine()
        steps = [
            {
                "name": "review",
                "agent": "a/B/v1",
                "gate": {
                    "name": "g_review",
                    "condition": "always_fail",
                    "on_fail": "escalate_human",
                },
            }
        ]
        result = await engine.execute(steps)
        assert result["status"] == "error"
        assert "escalated to human" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_gate_degrade_continues(self) -> None:
        """degrade gate action allows workflow to continue."""
        engine = CompositionEngine()
        steps = [
            {
                "name": "optional",
                "agent": "a/B/v1",
                "gate": {
                    "name": "g_opt",
                    "condition": "always_fail",
                    "on_fail": "degrade",
                },
            },
            {"name": "final", "agent": "a/C/v1"},
        ]
        result = await engine.execute(steps)
        assert result["status"] == "completed"
        assert "final" in result["step_results"]

    @pytest.mark.asyncio
    async def test_gate_fallback_continues(self) -> None:
        """fallback gate action allows workflow to continue."""
        engine = CompositionEngine()
        steps = [
            {
                "name": "risky",
                "agent": "a/B/v1",
                "gate": {
                    "name": "g_risky",
                    "condition": "always_fail",
                    "on_fail": "fallback",
                },
            },
            {"name": "safe", "agent": "a/C/v1"},
        ]
        result = await engine.execute(steps)
        assert result["status"] == "completed"


class TestCrossStageFeedback:
    """Task 69: cross-stage feedback via FeedbackLoop integration."""

    @pytest.mark.asyncio
    async def test_register_feedback_loop(self) -> None:
        """Can register and query feedback loops."""
        engine = CompositionEngine()
        loop = FeedbackLoop(from_step="test", to_step="codegen", max_iterations=2)
        engine.register_feedback_loop(loop)
        assert len(engine._feedback_loops) == 1

    @pytest.mark.asyncio
    async def test_feedback_loop_triggers(self) -> None:
        """Feedback loop triggers when step fails."""
        engine = CompositionEngine()
        loop = FeedbackLoop(
            from_step="test",
            to_step="codegen",
            condition_fn=lambda r: r.get("status") == "failed",
            max_iterations=2,
        )
        engine.register_feedback_loop(loop)

        feedback = engine._check_feedback_loops("test", {"status": "failed"})
        assert feedback is not None
        assert feedback["from_step"] == "test"
        assert feedback["to_step"] == "codegen"
        assert loop.iterations_used == 1

    @pytest.mark.asyncio
    async def test_feedback_loop_does_not_trigger_on_success(self) -> None:
        """Feedback loop does not trigger when step succeeds."""
        engine = CompositionEngine()
        loop = FeedbackLoop(
            from_step="test",
            to_step="codegen",
            condition_fn=lambda r: r.get("status") == "failed",
        )
        engine.register_feedback_loop(loop)

        feedback = engine._check_feedback_loops("test", {"status": "completed"})
        assert feedback is None

    @pytest.mark.asyncio
    async def test_feedback_loop_respects_max_iterations(self) -> None:
        """After max iterations, feedback loop stops triggering."""
        engine = CompositionEngine()
        loop = FeedbackLoop(
            from_step="test",
            to_step="codegen",
            condition_fn=lambda r: r.get("status") == "failed",
            max_iterations=1,
        )
        engine.register_feedback_loop(loop)

        fb1 = engine._check_feedback_loops("test", {"status": "failed"})
        assert fb1 is not None

        fb2 = engine._check_feedback_loops("test", {"status": "failed"})
        assert fb2 is None  # exhausted

    @pytest.mark.asyncio
    async def test_feedback_loop_logs_event(self) -> None:
        """Feedback loop trigger is logged."""
        engine = CompositionEngine()
        loop = FeedbackLoop(
            from_step="test",
            to_step="codegen",
            condition_fn=lambda r: True,
            max_iterations=2,
        )
        engine.register_feedback_loop(loop)
        engine._check_feedback_loops("test", {"status": "failed"})

        log = engine.execution_log
        assert any(e["event"] == "feedback_loop_triggered" for e in log)
