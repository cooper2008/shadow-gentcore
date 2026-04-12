"""BudgetTracker — enforces token and cost limits during agent execution."""

from __future__ import annotations

from typing import Any


class BudgetExceededError(Exception):
    """Raised when a budget limit is exceeded."""


class BudgetTracker:
    """Tracks and enforces token and cost budgets for agent runs.

    Supports:
    - Per-task token limits
    - Per-task cost limits (USD)
    - Cumulative tracking across multiple calls
    - Pre-flight budget checks before LLM calls
    """

    def __init__(
        self,
        max_tokens: int | None = None,
        max_cost_usd: float | None = None,
    ) -> None:
        self.max_tokens = max_tokens
        self.max_cost_usd = max_cost_usd
        self._tokens_used: int = 0
        self._cost_usd: float = 0.0
        self._call_count: int = 0

    def record_usage(self, tokens: int, cost_usd: float = 0.0) -> None:
        """Record token and cost usage from a provider call.

        Raises BudgetExceededError if limits are breached after recording.
        """
        self._tokens_used += tokens
        self._cost_usd += cost_usd
        self._call_count += 1

        self._check_limits()

    def check_budget(self, estimated_tokens: int = 0, estimated_cost: float = 0.0) -> bool:
        """Pre-flight check: would this call exceed the budget?

        Returns True if the estimated call fits within budget.
        """
        if self.max_tokens is not None:
            if self._tokens_used + estimated_tokens > self.max_tokens:
                return False
        if self.max_cost_usd is not None:
            if self._cost_usd + estimated_cost > self.max_cost_usd:
                return False
        return True

    def _check_limits(self) -> None:
        """Check if current usage exceeds budget limits."""
        if self.max_tokens is not None and self._tokens_used > self.max_tokens:
            raise BudgetExceededError(
                f"Token budget exceeded: {self._tokens_used}/{self.max_tokens} tokens used"
            )
        if self.max_cost_usd is not None and self._cost_usd > self.max_cost_usd:
            raise BudgetExceededError(
                f"Cost budget exceeded: ${self._cost_usd:.4f}/${self.max_cost_usd:.4f} USD"
            )

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def cost_usd(self) -> float:
        return self._cost_usd

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def tokens_remaining(self) -> int | None:
        if self.max_tokens is None:
            return None
        return max(0, self.max_tokens - self._tokens_used)

    @property
    def cost_remaining(self) -> float | None:
        if self.max_cost_usd is None:
            return None
        return max(0.0, self.max_cost_usd - self._cost_usd)

    def summary(self) -> dict[str, Any]:
        """Return a summary dict of budget usage."""
        return {
            "tokens_used": self._tokens_used,
            "tokens_limit": self.max_tokens,
            "tokens_remaining": self.tokens_remaining,
            "cost_usd": self._cost_usd,
            "cost_limit": self.max_cost_usd,
            "cost_remaining": self.cost_remaining,
            "call_count": self._call_count,
        }

    def reset(self) -> None:
        """Reset all usage counters."""
        self._tokens_used = 0
        self._cost_usd = 0.0
        self._call_count = 0
