"""EvaluatorLoop — Planner → Generator → Evaluator cycle with configurable rounds."""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

# Type alias for async step functions used in the loop
StepFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class EvaluatorLoop:
    """Runs a Planner → Generator → Evaluator cycle.

    The loop:
    1. Planner produces a FeatureContract (criteria)
    2. Generator produces output
    3. Evaluator grades output against the contract
    4. If evaluation fails and rounds remain, feed critique back to Generator
    5. Repeat until pass or max_rounds exhausted

    Args:
        planner_fn: Async function that takes task context and returns a contract.
        generator_fn: Async function that takes task+contract+feedback and returns output.
        evaluator_fn: Async function that takes contract+output and returns evaluation result.
        max_rounds: Maximum generator→evaluator iterations (default 3).
        threshold: Minimum score to consider passing (default 1.0 = all criteria).
    """

    def __init__(
        self,
        planner_fn: StepFn,
        generator_fn: StepFn,
        evaluator_fn: StepFn,
        max_rounds: int = 3,
        threshold: float = 1.0,
    ) -> None:
        self._planner_fn = planner_fn
        self._generator_fn = generator_fn
        self._evaluator_fn = evaluator_fn
        self._max_rounds = max_rounds
        self._threshold = threshold
        self._history: list[dict[str, Any]] = []

    async def run(self, task_context: dict[str, Any]) -> dict[str, Any]:
        """Execute the evaluator loop.

        Returns:
            Dict with keys: status, contract, output, evaluation, rounds, history.
        """
        self._history = []

        # Step 1: Plan — produce FeatureContract
        contract = await self._planner_fn(task_context)
        self._history.append({"phase": "plan", "result": contract})
        logger.info("EvaluatorLoop: contract produced with %d criteria", len(contract.get("criteria", [])))

        feedback: str | None = None
        output: dict[str, Any] = {}
        evaluation: dict[str, Any] = {}

        for round_num in range(1, self._max_rounds + 1):
            # Step 2: Generate
            gen_input = {
                "task": task_context,
                "contract": contract,
                "feedback": feedback,
                "round": round_num,
            }
            output = await self._generator_fn(gen_input)
            self._history.append({"phase": "generate", "round": round_num, "result": output})

            # Step 3: Evaluate
            eval_input = {"contract": contract, "output": output, "round": round_num}
            evaluation = await self._evaluator_fn(eval_input)
            self._history.append({"phase": "evaluate", "round": round_num, "result": evaluation})

            score = evaluation.get("score", 0.0)
            overall_pass = evaluation.get("overall_pass", False)
            logger.info("EvaluatorLoop: round %d — score=%.2f, pass=%s", round_num, score, overall_pass)

            if overall_pass or score >= self._threshold:
                return {
                    "status": "passed",
                    "contract": contract,
                    "output": output,
                    "evaluation": evaluation,
                    "rounds": round_num,
                    "history": self._history,
                }

            # Feed critique back for next round
            feedback = evaluation.get("feedback", "Criteria not met. Please revise.")

        # Exhausted all rounds
        return {
            "status": "failed",
            "contract": contract,
            "output": output,
            "evaluation": evaluation,
            "rounds": self._max_rounds,
            "history": self._history,
        }

    @property
    def history(self) -> list[dict[str, Any]]:
        return list(self._history)
