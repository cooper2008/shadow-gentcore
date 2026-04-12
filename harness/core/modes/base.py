"""Base execution strategy interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


def _resp_get(response: Any, key: str, default: Any = None) -> Any:
    """Get a field from an LLM response (works with both dict and dataclass).

    AnthropicProvider returns LLMResponse (dataclass), DryRunProvider returns dict.
    This helper normalizes access so execution strategies work with both.
    """
    if isinstance(response, dict):
        return response.get(key, default)
    return getattr(response, key, default)


class ExecutionStrategy(ABC):
    """Abstract base for all execution mode strategies.

    Each strategy defines how an agent interacts with the LLM provider:
    - ReAct: think → tool call → observe → repeat
    - PlanExecute: plan phase → execute phase
    - ChainOfThought: single deep reasoning pass
    """

    @abstractmethod
    async def execute(
        self,
        messages: list[dict[str, Any]],
        provider: Any,
        tool_executor: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute the strategy and return the result.

        Args:
            messages: Assembled LLM messages from PromptAssembler.
            provider: LLM provider instance (BaseProvider).
            tool_executor: Optional ToolExecutor for strategies that use tools.

        Returns:
            Dict with 'content', 'tool_calls', 'tokens_used', 'steps' keys.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the strategy name."""
        ...
