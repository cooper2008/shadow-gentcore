"""ChainOfThought execution strategy — single deep reasoning pass, no tool loop."""

from __future__ import annotations

from typing import Any

from harness.core.modes.base import ExecutionStrategy, _resp_get


class ChainOfThoughtStrategy(ExecutionStrategy):
    """Single-pass deep reasoning without tool interaction.

    Sends messages to LLM once and returns the result.
    Useful for analysis, review, and reasoning-heavy tasks
    that don't require external tool calls.
    """

    def __init__(self, **kwargs: Any) -> None:
        pass

    @property
    def name(self) -> str:
        return "chain_of_thought"

    async def execute(
        self,
        messages: list[dict[str, Any]],
        provider: Any,
        tool_executor: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        output_schema = kwargs.get("output_schema")
        chat_kwargs: dict[str, Any] = {}
        if output_schema:
            chat_kwargs["output_schema"] = output_schema
        response = await provider.chat(messages, **chat_kwargs)

        return {
            "content": _resp_get(response,"content", ""),
            "tool_calls": [],
            "tokens_used": _resp_get(response,"tokens_used", 0),
            "steps": [{
                "step": 1,
                "type": "reason",
                "content": _resp_get(response,"content", ""),
            }],
        }
