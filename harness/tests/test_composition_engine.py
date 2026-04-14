"""Tests for CompositionEngine."""

from __future__ import annotations

import logging

import pytest

from harness.core.composition_engine import CompositionEngine, GateFailure


class TestCompositionEngine:
    @pytest.mark.asyncio
    async def test_linear_3_step_workflow(self) -> None:
        engine = CompositionEngine()
        steps = [
            {"name": "codegen", "agent": "CodeGenAgent"},
            {"name": "validate", "agent": "ValidateAgent", "depends_on": ["codegen"]},
            {"name": "test", "agent": "TestAgent", "depends_on": ["validate"]},
        ]
        result = await engine.execute(steps)
        assert result["status"] == "completed"
        assert "codegen" in result["step_results"]
        assert "validate" in result["step_results"]
        assert "test" in result["step_results"]

    @pytest.mark.asyncio
    async def test_step_with_passing_gate(self) -> None:
        engine = CompositionEngine()
        steps = [
            {"name": "codegen", "agent": "CodeGenAgent", "gate": {
                "name": "codegen_gate", "condition": "true",
            }},
        ]
        result = await engine.execute(steps)
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_gate_abort(self) -> None:
        engine = CompositionEngine()
        steps = [
            {"name": "codegen", "agent": "CodeGenAgent", "gate": {
                "name": "quality_gate", "condition": "always_fail", "on_fail": "abort",
            }},
        ]
        result = await engine.execute(steps)
        assert result["status"] == "error"
        assert "gate" in result.get("error", "").lower() or "abort" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_gate_retry_then_fail(self) -> None:
        engine = CompositionEngine()
        steps = [
            {"name": "step1", "agent": "Agent1", "gate": {
                "name": "retry_gate", "condition": "always_fail",
                "on_fail": "retry", "max_retries": 2,
            }},
        ]
        result = await engine.execute(steps)
        assert result["status"] == "gate_failed"
        # Should see retry events in log
        retry_events = [e for e in result["execution_log"] if e.get("event") == "gate_retry"]
        assert len(retry_events) == 2

    @pytest.mark.asyncio
    async def test_gate_degrade_continues(self) -> None:
        engine = CompositionEngine()
        steps = [
            {"name": "step1", "agent": "Agent1", "gate": {
                "name": "soft_gate", "condition": "always_fail", "on_fail": "degrade",
            }},
            {"name": "step2", "agent": "Agent2", "depends_on": ["step1"]},
        ]
        result = await engine.execute(steps)
        assert result["status"] == "completed"
        assert "step2" in result["step_results"]

    @pytest.mark.asyncio
    async def test_dependency_artifacts_passed(self) -> None:
        engine = CompositionEngine()
        steps = [
            {"name": "producer", "agent": "A"},
            {"name": "consumer", "agent": "B", "depends_on": ["producer"]},
        ]
        result = await engine.execute(steps)
        consumer_result = result["step_results"]["consumer"]
        assert "producer" in consumer_result.get("dependencies", {})

    @pytest.mark.asyncio
    async def test_execution_log(self) -> None:
        engine = CompositionEngine()
        steps = [
            {"name": "s1", "agent": "A"},
            {"name": "s2", "agent": "B"},
        ]
        result = await engine.execute(steps)
        started = [e for e in result["execution_log"] if e["event"] == "step_started"]
        completed = [e for e in result["execution_log"] if e["event"] == "step_completed"]
        assert len(started) == 2
        assert len(completed) == 2

    @pytest.mark.asyncio
    async def test_mock_output_config(self) -> None:
        engine = CompositionEngine()
        steps = [{"name": "s1", "agent": "A"}]
        result = await engine.execute(steps, step_configs={
            "s1": {"mock_output": "custom output"},
        })
        assert result["step_results"]["s1"]["output"] == "custom output"

    @pytest.mark.asyncio
    async def test_reset(self) -> None:
        engine = CompositionEngine()
        await engine.execute([{"name": "s1", "agent": "A"}])
        assert len(engine.step_results) == 1
        engine.reset()
        assert len(engine.step_results) == 0
        assert len(engine.execution_log) == 0


class TestEvaluateCondition:
    """Unit tests for the fail-closed _evaluate_condition method."""

    def setup_method(self) -> None:
        self.engine = CompositionEngine()

    # ── Pass-through conditions ──────────────────────────────────────────

    def test_true_returns_true(self) -> None:
        assert self.engine._evaluate_condition("true", {}) is True

    def test_always_pass_returns_true(self) -> None:
        assert self.engine._evaluate_condition("always_pass", {}) is True

    def test_false_returns_false(self) -> None:
        assert self.engine._evaluate_condition("false", {}) is False

    def test_always_fail_returns_false(self) -> None:
        assert self.engine._evaluate_condition("always_fail", {}) is False

    # ── Status conditions ────────────────────────────────────────────────

    def test_status_success_with_success(self) -> None:
        assert self.engine._evaluate_condition("status == success", {"status": "success"}) is True

    def test_status_success_with_completed(self) -> None:
        assert self.engine._evaluate_condition("status == success", {"status": "completed"}) is True

    def test_status_success_with_failed(self) -> None:
        assert self.engine._evaluate_condition("status == success", {"status": "failed"}) is False

    def test_status_completed_with_completed(self) -> None:
        assert self.engine._evaluate_condition("status == completed", {"status": "completed"}) is True

    def test_status_completed_with_success(self) -> None:
        assert self.engine._evaluate_condition("status == completed", {"status": "success"}) is True

    def test_status_completed_with_error(self) -> None:
        assert self.engine._evaluate_condition("status == completed", {"status": "error"}) is False

    # ── has_output condition ─────────────────────────────────────────────

    def test_has_output_with_output_key(self) -> None:
        assert self.engine._evaluate_condition("has_output", {"output": "some text"}) is True

    def test_has_output_with_content_key(self) -> None:
        assert self.engine._evaluate_condition("has_output", {"content": "some text"}) is True

    def test_has_output_with_empty_output(self) -> None:
        assert self.engine._evaluate_condition("has_output", {"output": ""}) is False

    def test_has_output_with_no_output_keys(self) -> None:
        assert self.engine._evaluate_condition("has_output", {"status": "completed"}) is False

    # ── score >= N conditions ────────────────────────────────────────────

    def test_score_gte_passes_above_threshold(self) -> None:
        result = {"_validation": {"score": 0.8}}
        assert self.engine._evaluate_condition("score >= 0.7", result) is True

    def test_score_gte_passes_at_threshold(self) -> None:
        result = {"_validation": {"score": 0.7}}
        assert self.engine._evaluate_condition("score >= 0.7", result) is True

    def test_score_gte_fails_below_threshold(self) -> None:
        result = {"_validation": {"score": 0.5}}
        assert self.engine._evaluate_condition("score >= 0.7", result) is False

    def test_score_gte_missing_validation_defaults_zero(self) -> None:
        # No _validation key → score defaults to 0, fails against 0.7
        assert self.engine._evaluate_condition("score >= 0.7", {}) is False

    def test_score_gte_missing_score_key_defaults_zero(self) -> None:
        result = {"_validation": {}}
        assert self.engine._evaluate_condition("score >= 0.5", result) is False

    def test_score_gte_integer_threshold(self) -> None:
        result = {"_validation": {"score": 1.0}}
        assert self.engine._evaluate_condition("score >= 1", result) is True

    def test_score_gte_with_whitespace_variants(self) -> None:
        result = {"_validation": {"score": 0.9}}
        assert self.engine._evaluate_condition("score>=0.8", result) is True

    # ── Fail-closed for unknown conditions ───────────────────────────────

    def test_unknown_condition_returns_false(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="harness.core.composition_engine"):
            result = self.engine._evaluate_condition("output_contains:foo", {"output": "foo bar"})
        assert result is False
        assert "Unrecognized gate condition" in caplog.text

    def test_empty_string_condition_returns_false(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="harness.core.composition_engine"):
            result = self.engine._evaluate_condition("", {})
        assert result is False

    def test_arbitrary_string_condition_returns_false(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="harness.core.composition_engine"):
            result = self.engine._evaluate_condition("result_valid", {"result": "ok"})
        assert result is False
        assert "Unrecognized gate condition" in caplog.text

    def test_unknown_condition_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="harness.core.composition_engine"):
            self.engine._evaluate_condition("score_check", {})
        assert "score_check" in caplog.text
