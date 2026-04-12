"""Tests for CompositionEngine."""

from __future__ import annotations

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
