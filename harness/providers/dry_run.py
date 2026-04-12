"""DryRunProvider — mock LLM provider for testing and dry-runs without API keys."""

from __future__ import annotations

from typing import Any, AsyncIterator

from harness.providers.base_provider import BaseProvider, LLMResponse, LLMChunk


class DryRunProvider(BaseProvider):
    """Returns stub responses without calling any external API.

    Use for:
    - ``./ai run agent --dry-run``
    - ``./ai run workflow --dry-run``
    - Testing agent manifests and workflows before using real tokens
    - CI/CD pipeline validation
    """

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """Return a stub response dict compatible with execution strategies.

        Returns a plain dict (not LLMResponse) because execution strategies
        use `response.get("content")` / `response.get("tool_calls")`.
        """
        # Extract task summary from the last user message
        task_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                task_text = str(msg.get("content", ""))[:300]
                break

        # Extract agent identity from system message
        agent_identity = ""
        for msg in messages:
            if msg.get("role") == "system":
                content = str(msg.get("content", ""))
                first_line = content.split("\n")[0][:100]
                agent_identity = first_line
                break

        content = (
            f"[DRY RUN] Agent: {agent_identity}\n"
            f"Task: {task_text}\n\n"
            f"This is a dry-run response. In a real execution, the agent would:\n"
            f"1. Analyze the task using its execution strategy\n"
            f"2. Call tools as needed (file_read, file_write, search_code, etc.)\n"
            f"3. Produce structured output matching the output_schema\n\n"
            f"Status: completed (dry-run)"
        )

        return {
            "content": content,
            "tokens_used": 0,
            "tool_calls": [],
            "model": "dry-run",
        }

    async def stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> AsyncIterator[LLMChunk]:
        """Yield a single dry-run chunk."""
        response = await self.chat(messages, **kwargs)
        yield LLMChunk(
            content=response["content"],
            delta=response["content"],
            is_final=True,
            tokens_used=0,
        )

    @property
    def provider_name(self) -> str:
        return "dry_run"

    @property
    def default_model(self) -> str:
        return "dry-run"
