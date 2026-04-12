"""FeedbackLoop — routes feedback from a downstream step back to an upstream step."""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

StepFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class FeedbackLoop:
    """Manages feedback routing between workflow steps.

    When a downstream step (e.g., Test) produces feedback indicating failure,
    this loop re-runs the upstream step (e.g., CodeGen) with the feedback,
    then re-runs intermediate steps up to and including the feedback source.

    Args:
        from_step: Name of the step producing feedback.
        to_step: Name of the step that should receive feedback and re-run.
        condition_fn: Function that checks whether feedback should trigger re-run.
        max_iterations: Maximum number of feedback loop iterations.
    """

    def __init__(
        self,
        from_step: str,
        to_step: str,
        condition_fn: Callable[[dict[str, Any]], bool] | None = None,
        max_iterations: int = 2,
    ) -> None:
        self.from_step = from_step
        self.to_step = to_step
        self._condition_fn = condition_fn or (lambda result: not result.get("success", True))
        self._max_iterations = max_iterations
        self._iteration_count = 0
        self._history: list[dict[str, Any]] = []

    def should_trigger(self, step_result: dict[str, Any]) -> bool:
        """Check if the feedback loop should trigger based on step result."""
        if self._iteration_count >= self._max_iterations:
            logger.info(
                "FeedbackLoop %s→%s: max iterations (%d) reached",
                self.from_step, self.to_step, self._max_iterations,
            )
            return False
        return self._condition_fn(step_result)

    def record_iteration(self, feedback: dict[str, Any]) -> None:
        """Record a feedback iteration."""
        self._iteration_count += 1
        self._history.append({
            "iteration": self._iteration_count,
            "from_step": self.from_step,
            "to_step": self.to_step,
            "feedback": feedback,
        })
        logger.info(
            "FeedbackLoop %s→%s: iteration %d/%d",
            self.from_step, self.to_step,
            self._iteration_count, self._max_iterations,
        )

    def reset(self) -> None:
        """Reset iteration count and history."""
        self._iteration_count = 0
        self._history.clear()

    @property
    def iterations_used(self) -> int:
        return self._iteration_count

    @property
    def iterations_remaining(self) -> int:
        return max(0, self._max_iterations - self._iteration_count)

    @property
    def history(self) -> list[dict[str, Any]]:
        return list(self._history)

    @property
    def exhausted(self) -> bool:
        return self._iteration_count >= self._max_iterations
