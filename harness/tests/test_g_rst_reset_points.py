"""Tests for G-RST — wire reset_points in composition_engine (fix/G-RST-reset-points).

The `reset_points:` field on WorkflowDefinition declared safe rollback targets
but was dead config — no code referenced it. This fix:

1. Validates at workflow load that every `fallback_step` in a step gate is
   included in `reset_points` (when declared).
2. Records which reset point was used in rollback execution log events.
"""

from __future__ import annotations

import pytest

from harness.core.composition_engine import (
    CompositionEngine,
    ExecutionEvent,
    validate_reset_points,
)


class TestResetPointValidation:
    def test_passes_when_no_reset_points_declared(self) -> None:
        """Backward compat: workflows without reset_points skip validation."""
        steps = [
            {"name": "a", "agent": "A"},
            {"name": "b", "agent": "B", "depends_on": ["a"],
             "gate": {"condition": "s == 1", "on_fail": "rollback",
                      "fallback_step": "a"}},
        ]
        # No reset_points → validation is a no-op
        validate_reset_points(steps, reset_points=[])

    def test_passes_when_fallback_is_in_reset_points(self) -> None:
        steps = [
            {"name": "a", "agent": "A"},
            {"name": "b", "agent": "B", "depends_on": ["a"]},
            {"name": "c", "agent": "C", "depends_on": ["b"],
             "gate": {"condition": "s == 1", "on_fail": "rollback",
                      "fallback_step": "a"}},
        ]
        validate_reset_points(steps, reset_points=["a", "b"])

    def test_rejects_fallback_not_in_reset_points(self) -> None:
        steps = [
            {"name": "a", "agent": "A"},
            {"name": "b", "agent": "B", "depends_on": ["a"]},
            {"name": "c", "agent": "C", "depends_on": ["b"],
             "gate": {"condition": "s == 1", "on_fail": "rollback",
                      "fallback_step": "b"}},
        ]
        with pytest.raises(ValueError) as exc_info:
            validate_reset_points(steps, reset_points=["a"])
        assert "fallback_step" in str(exc_info.value).lower()
        assert "'b'" in str(exc_info.value) or "b" in str(exc_info.value)

    def test_rejects_rollback_to_not_in_reset_points(self) -> None:
        """rollback_to is the alternate naming for the same concept."""
        steps = [
            {"name": "a", "agent": "A"},
            {"name": "b", "agent": "B", "depends_on": ["a"],
             "gate": {"condition": "s == 1", "on_fail": "rollback",
                      "rollback_to": "ghost_step"}},
        ]
        with pytest.raises(ValueError):
            validate_reset_points(steps, reset_points=["a"])

    def test_skips_gates_without_rollback_action(self) -> None:
        """Only on_fail=rollback | fallback matters; retry/abort/etc. don't use reset points."""
        steps = [
            {"name": "a", "agent": "A"},
            {"name": "b", "agent": "B", "depends_on": ["a"],
             "gate": {"condition": "s == 1", "on_fail": "retry",
                      "fallback_step": "ghost"}},
        ]
        # retry doesn't use reset_points → no validation error
        validate_reset_points(steps, reset_points=["a"])

    def test_validates_fallback_on_fallback_action(self) -> None:
        steps = [
            {"name": "a", "agent": "A"},
            {"name": "b", "agent": "B", "depends_on": ["a"],
             "gate": {"condition": "s == 1", "on_fail": "fallback",
                      "fallback_step": "ghost"}},
        ]
        with pytest.raises(ValueError):
            validate_reset_points(steps, reset_points=["a"])

    def test_reports_multiple_errors_in_one_message(self) -> None:
        steps = [
            {"name": "a", "agent": "A"},
            {"name": "b", "agent": "B",
             "gate": {"condition": "s == 1", "on_fail": "rollback",
                      "fallback_step": "ghost1"}},
            {"name": "c", "agent": "C",
             "gate": {"condition": "s == 1", "on_fail": "rollback",
                      "fallback_step": "ghost2"}},
        ]
        with pytest.raises(ValueError) as exc_info:
            validate_reset_points(steps, reset_points=["a"])
        msg = str(exc_info.value)
        assert "ghost1" in msg
        assert "ghost2" in msg


class TestResetPointRollbackObservability:
    @pytest.mark.asyncio
    async def test_rollback_event_carries_reset_point(self) -> None:
        engine = CompositionEngine()
        engine._reset_points = ["a"]  # declared before execute

        # Pre-populate step results so rollback has something to clear
        engine._step_results = {"a": {"status": "completed"}, "b": {"status": "failed"}}

        gate = {"name": "b_gate", "condition": "status == success",
                "on_fail": "rollback", "rollback_to": "a"}
        result = {"status": "failed", "output": "bad"}
        step = {"name": "b", "agent": "B"}

        passed = await engine._check_gate("b", result, gate, config={}, step=step)
        assert passed is False

        rollback_events = [
            e for e in engine.execution_log
            if e.get("event") == ExecutionEvent.GATE_ROLLBACK
        ]
        assert len(rollback_events) == 1
        assert rollback_events[0]["rollback_to"] == "a"
        # New: event should indicate whether the target was a declared reset_point
        assert rollback_events[0].get("is_reset_point") is True

    @pytest.mark.asyncio
    async def test_rollback_event_flags_non_reset_point(self) -> None:
        engine = CompositionEngine()
        engine._reset_points = ["a"]
        engine._step_results = {"c": {"status": "completed"}, "b": {"status": "failed"}}

        gate = {"name": "b_gate", "condition": "status == success",
                "on_fail": "rollback", "rollback_to": "c"}  # c not in reset_points
        step = {"name": "b", "agent": "B"}

        await engine._check_gate("b", {"status": "failed"}, gate, config={}, step=step)

        rollback_events = [
            e for e in engine.execution_log
            if e.get("event") == ExecutionEvent.GATE_ROLLBACK
        ]
        assert len(rollback_events) == 1
        assert rollback_events[0]["rollback_to"] == "c"
        assert rollback_events[0].get("is_reset_point") is False
