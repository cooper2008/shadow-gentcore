"""Tests for FeedbackLoop."""

from __future__ import annotations

import pytest

from harness.core.feedback_loop import FeedbackLoop


class TestFeedbackLoop:
    def test_should_trigger_on_failure(self) -> None:
        loop = FeedbackLoop(from_step="test", to_step="codegen")
        assert loop.should_trigger({"success": False}) is True

    def test_should_not_trigger_on_success(self) -> None:
        loop = FeedbackLoop(from_step="test", to_step="codegen")
        assert loop.should_trigger({"success": True}) is False

    def test_custom_condition(self) -> None:
        loop = FeedbackLoop(
            from_step="test", to_step="codegen",
            condition_fn=lambda r: r.get("failed", 0) > 0,
        )
        assert loop.should_trigger({"failed": 3}) is True
        assert loop.should_trigger({"failed": 0}) is False

    def test_max_iterations_respected(self) -> None:
        loop = FeedbackLoop(from_step="test", to_step="codegen", max_iterations=2)
        assert loop.should_trigger({"success": False}) is True
        loop.record_iteration({"feedback": "fix 1"})
        assert loop.should_trigger({"success": False}) is True
        loop.record_iteration({"feedback": "fix 2"})
        assert loop.should_trigger({"success": False}) is False

    def test_iterations_tracking(self) -> None:
        loop = FeedbackLoop(from_step="test", to_step="codegen", max_iterations=3)
        assert loop.iterations_used == 0
        assert loop.iterations_remaining == 3
        assert loop.exhausted is False

        loop.record_iteration({"feedback": "attempt 1"})
        assert loop.iterations_used == 1
        assert loop.iterations_remaining == 2

    def test_exhausted_property(self) -> None:
        loop = FeedbackLoop(from_step="test", to_step="codegen", max_iterations=1)
        loop.record_iteration({"feedback": "only try"})
        assert loop.exhausted is True

    def test_history(self) -> None:
        loop = FeedbackLoop(from_step="test", to_step="codegen", max_iterations=3)
        loop.record_iteration({"feedback": "fix A"})
        loop.record_iteration({"feedback": "fix B"})

        history = loop.history
        assert len(history) == 2
        assert history[0]["iteration"] == 1
        assert history[0]["from_step"] == "test"
        assert history[0]["to_step"] == "codegen"
        assert history[1]["feedback"]["feedback"] == "fix B"

    def test_reset(self) -> None:
        loop = FeedbackLoop(from_step="test", to_step="codegen", max_iterations=2)
        loop.record_iteration({"feedback": "x"})
        loop.record_iteration({"feedback": "y"})
        assert loop.exhausted is True

        loop.reset()
        assert loop.iterations_used == 0
        assert loop.iterations_remaining == 2
        assert loop.exhausted is False
        assert loop.history == []

    def test_step_names(self) -> None:
        loop = FeedbackLoop(from_step="validate", to_step="codegen")
        assert loop.from_step == "validate"
        assert loop.to_step == "codegen"
