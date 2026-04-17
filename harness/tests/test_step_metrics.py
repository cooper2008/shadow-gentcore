"""Tests for G7 — per-step retry/token/cost/duration observability."""

from __future__ import annotations

from typing import Any

import pytest

from harness.core.composition_engine import CompositionEngine, ExecutionEvent


class _FakeRunner:
    """Minimal AgentRunner stub that returns a scripted result per step.

    Cycles through `results` in order of call. Each result is a dict the
    CompositionEngine will treat as the step output.
    """

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self._results = list(results)
        self._idx = 0

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        result = self._results[self._idx]
        self._idx += 1
        return result


@pytest.fixture()
def engine_with_results():
    def _make(results: list[dict[str, Any]]) -> CompositionEngine:
        return CompositionEngine(agent_runner=_FakeRunner(results))
    return _make


async def _run_step(engine: CompositionEngine, step_name: str, agent_id: str = "domain/X/v1", **cfg: Any) -> dict[str, Any]:
    """Run a single step through _execute_step with a minimal config."""
    config = {"manifest": {"id": agent_id}, "task": {}, "system_prompt": "", **cfg}
    return await engine._execute_step(step_name, agent_id, config, dep_artifacts={})


class TestMetricsInitialisation:
    def test_fresh_engine_has_empty_metrics(self) -> None:
        engine = CompositionEngine()
        assert engine.get_step_metrics() == {}

    def test_unknown_step_returns_empty_dict(self) -> None:
        engine = CompositionEngine()
        assert engine.get_step_metrics("never_ran") == {}


class TestTokenAndCostAttribution:
    @pytest.mark.asyncio
    async def test_step_tokens_recorded(self, engine_with_results) -> None:
        engine = engine_with_results([
            {"output": "ok", "tokens_used": 1234, "status": "success"},
        ])
        await _run_step(engine, "scan")
        metrics = engine.get_step_metrics("scan")
        assert metrics["tokens_used"] == 1234
        assert metrics["retry_count"] == 0
        assert metrics["cost_usd"] == 0.0
        assert metrics["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_step_cost_recorded(self, engine_with_results) -> None:
        engine = engine_with_results([
            {"output": "ok", "tokens_used": 100, "cost_usd": 0.42, "status": "success"},
        ])
        await _run_step(engine, "scan")
        metrics = engine.get_step_metrics("scan")
        assert metrics["cost_usd"] == 0.42

    @pytest.mark.asyncio
    async def test_missing_tokens_defaults_to_zero(self, engine_with_results) -> None:
        engine = engine_with_results([
            {"output": "ok", "status": "success"},
        ])
        await _run_step(engine, "scan")
        metrics = engine.get_step_metrics("scan")
        assert metrics["tokens_used"] == 0


class TestStepCompletedEventPayload:
    """STEP_COMPLETED events are emitted by _run_dag_step (the DAG wrapper)."""

    @pytest.mark.asyncio
    async def test_event_contains_observability_fields(self, engine_with_results) -> None:
        engine = engine_with_results([
            {"output": "ok", "tokens_used": 50, "cost_usd": 0.01, "status": "success"},
        ])
        config = {"manifest": {"id": "d/X/v1"}, "task": {}, "system_prompt": ""}
        await engine._run_dag_step("scan", {"name": "scan", "agent": "d/X/v1"}, config, {})
        step_events = [e for e in engine.execution_log if e.get("event") == ExecutionEvent.STEP_COMPLETED]
        assert len(step_events) == 1
        evt = step_events[0]
        assert evt["step"] == "scan"
        assert evt["status"] == "success"
        assert evt["tokens_used"] == 50
        assert evt["cost_usd"] == 0.01
        assert evt["retry_count"] == 0
        assert "duration_ms" in evt
        assert evt["duration_ms"] >= 0


class TestRetryCountTracking:
    @pytest.mark.asyncio
    async def test_retry_count_surfaced_on_step_completed(self, engine_with_results) -> None:
        """Synthesize retries by bumping the metric directly — exercises the
        accumulator + event surfacing without wiring a full gate (gate retry
        path is covered by test_composition_engine)."""
        engine = engine_with_results([
            {"output": "ok", "tokens_used": 10, "status": "success"},
        ])
        engine._bump_step_metric("build", "retry_count", 1)
        engine._bump_step_metric("build", "retry_count", 1)
        engine._bump_step_metric("build", "retry_count", 1)
        config = {"manifest": {"id": "d/X/v1"}, "task": {}, "system_prompt": ""}
        await engine._run_dag_step("build", {"name": "build", "agent": "d/X/v1"}, config, {})

        step_events = [e for e in engine.execution_log if e.get("event") == ExecutionEvent.STEP_COMPLETED]
        assert step_events[0]["retry_count"] == 3


class TestAggregateApi:
    @pytest.mark.asyncio
    async def test_get_all_returns_full_map(self, engine_with_results) -> None:
        engine = engine_with_results([
            {"output": "a", "tokens_used": 10, "status": "success"},
            {"output": "b", "tokens_used": 20, "status": "success"},
        ])
        await _run_step(engine, "scan")
        await _run_step(engine, "build")
        all_metrics = engine.get_step_metrics()
        assert set(all_metrics.keys()) == {"scan", "build"}
        assert all_metrics["scan"]["tokens_used"] == 10
        assert all_metrics["build"]["tokens_used"] == 20

    @pytest.mark.asyncio
    async def test_reset_clears_metrics(self, engine_with_results) -> None:
        engine = engine_with_results([
            {"output": "ok", "tokens_used": 10, "status": "success"},
        ])
        await _run_step(engine, "scan")
        assert engine.get_step_metrics("scan")["tokens_used"] == 10
        engine.reset()
        assert engine.get_step_metrics() == {}

    @pytest.mark.asyncio
    async def test_get_step_metrics_returns_copy(self, engine_with_results) -> None:
        """Caller mutations must not affect internal state."""
        engine = engine_with_results([
            {"output": "ok", "tokens_used": 10, "status": "success"},
        ])
        await _run_step(engine, "scan")
        snapshot = engine.get_step_metrics("scan")
        snapshot["tokens_used"] = 999999
        # Internal state unchanged
        assert engine.get_step_metrics("scan")["tokens_used"] == 10
