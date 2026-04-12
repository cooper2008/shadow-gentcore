"""GradingEngine — evaluates agent output against FeatureContract criteria."""

from __future__ import annotations

from typing import Any

from agent_contracts.contracts.feature_contract import (
    CriterionResult,
    CriterionStatus,
    FeatureContract,
)


class GradingEngine:
    """Grades agent output against a FeatureContract.

    Supports two check types:
    - automated: runs a callable check function
    - llm_judge: delegates to an evaluator LLM (provider required)

    The engine populates the FeatureContract.results list and returns
    the overall pass/fail status and score.
    """

    def __init__(self) -> None:
        self._automated_checks: dict[str, Any] = {}

    def register_check(self, criterion_name: str, check_fn: Any) -> None:
        """Register an automated check function for a criterion.

        check_fn signature: (output: dict) -> (bool, str | None)
        Returns (passed, optional_reason).
        """
        self._automated_checks[criterion_name] = check_fn

    async def grade(
        self,
        contract: FeatureContract,
        agent_output: dict[str, Any],
        provider: Any | None = None,
    ) -> FeatureContract:
        """Grade agent output against all criteria in the contract.

        Args:
            contract: The FeatureContract defining criteria.
            agent_output: The agent's output to evaluate.
            provider: Optional LLM provider for llm_judge criteria.

        Returns:
            The contract with populated results.
        """
        results: list[CriterionResult] = []

        for criterion_desc in contract.criteria:
            # Check if there's a registered automated check
            check_fn = self._automated_checks.get(criterion_desc)
            if check_fn is not None:
                result = await self._run_automated_check(criterion_desc, check_fn, agent_output)
            elif provider is not None:
                result = await self._run_llm_judge(criterion_desc, agent_output, provider)
            else:
                result = CriterionResult(
                    name=criterion_desc,
                    status=CriterionStatus.SKIP,
                    reason="No check registered and no LLM provider available",
                )
            results.append(result)

        # Return a new contract with results populated
        return contract.model_copy(update={"results": results})

    async def _run_automated_check(
        self,
        criterion_name: str,
        check_fn: Any,
        agent_output: dict[str, Any],
    ) -> CriterionResult:
        """Run an automated check function."""
        try:
            passed, reason = check_fn(agent_output)
            return CriterionResult(
                name=criterion_name,
                status=CriterionStatus.PASS if passed else CriterionStatus.FAIL,
                reason=reason,
                score=1.0 if passed else 0.0,
            )
        except Exception as exc:
            return CriterionResult(
                name=criterion_name,
                status=CriterionStatus.FAIL,
                reason=f"Check raised exception: {exc}",
                score=0.0,
            )

    async def _run_llm_judge(
        self,
        criterion_desc: str,
        agent_output: dict[str, Any],
        provider: Any,
    ) -> CriterionResult:
        """Use an LLM as judge to evaluate a criterion."""
        import json

        judge_messages = [
            {
                "role": "system",
                "content": (
                    "You are a strict evaluator. Given an agent's output, determine "
                    "if the following criterion is satisfied. Respond with PASS or FAIL "
                    "followed by a brief reason."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Criterion: {criterion_desc}\n\n"
                    f"Agent Output:\n{json.dumps(agent_output, indent=2, default=str)}"
                ),
            },
        ]

        response = await provider.chat(judge_messages)
        content = response.get("content", "").strip()

        if content.upper().startswith("PASS"):
            return CriterionResult(
                name=criterion_desc,
                status=CriterionStatus.PASS,
                reason=content,
                score=1.0,
            )
        else:
            return CriterionResult(
                name=criterion_desc,
                status=CriterionStatus.FAIL,
                reason=content,
                score=0.0,
            )
