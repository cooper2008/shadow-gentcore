"""ContextEngine — builds and manages context for agent execution."""

from __future__ import annotations

from typing import Any


class ContextEngine:
    """Builds context from manifest, task, workspace, and prior artifacts.

    Responsibilities:
    - Gather context items from various sources
    - Priority ranking (high priority items first)
    - Token estimation and compaction
    - Reset policy support (clear context at checkpoints)
    """

    def __init__(self, max_tokens: int | None = None) -> None:
        self._max_tokens = max_tokens
        self._items: list[dict[str, Any]] = []

    def add_item(
        self,
        source: str,
        content: str,
        priority: int = 0,
        token_estimate: int | None = None,
    ) -> None:
        """Add a context item.

        Args:
            source: Origin of the context (e.g., 'repo_map', 'prior_artifact').
            content: The text content.
            priority: Higher = more important. Used for ranking.
            token_estimate: Estimated tokens. If None, uses len(content)//4.
        """
        if token_estimate is None:
            token_estimate = max(1, len(content) // 4)
        self._items.append({
            "source": source,
            "content": content,
            "priority": priority,
            "token_estimate": token_estimate,
        })

    def build(self) -> list[dict[str, str]]:
        """Build the final context list, sorted by priority and compacted to fit token budget.

        Returns:
            List of dicts with 'source' and 'content' keys, ready for PromptAssembler.
        """
        sorted_items = sorted(self._items, key=lambda x: x["priority"], reverse=True)

        if self._max_tokens is None:
            return [{"source": i["source"], "content": i["content"]} for i in sorted_items]

        result: list[dict[str, str]] = []
        tokens_used = 0
        for item in sorted_items:
            est = item["token_estimate"]
            if tokens_used + est > self._max_tokens:
                # Try to fit a truncated version
                remaining = self._max_tokens - tokens_used
                if remaining > 50:
                    ratio = remaining / est
                    truncated = item["content"][:int(len(item["content"]) * ratio)]
                    result.append({"source": item["source"], "content": truncated + "\n[truncated]"})
                    tokens_used += remaining
                break
            result.append({"source": item["source"], "content": item["content"]})
            tokens_used += est

        return result

    def reset(self) -> None:
        """Clear all context items (used at checkpoint reset points)."""
        self._items.clear()

    def checkpoint(self) -> dict[str, Any]:
        """Serialize current context state to a checkpoint dict.

        Returns:
            Dict with 'items' and 'max_tokens' keys for later restoration.
        """
        import copy
        return {
            "items": copy.deepcopy(self._items),
            "max_tokens": self._max_tokens,
        }

    def restore(self, checkpoint: dict[str, Any]) -> None:
        """Restore context from a checkpoint, replacing current state.

        Args:
            checkpoint: Dict produced by checkpoint() method.
        """
        import copy
        self._items = copy.deepcopy(checkpoint.get("items", []))
        if "max_tokens" in checkpoint:
            self._max_tokens = checkpoint["max_tokens"]

    def reset_to_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        """Clear current context and restore from checkpoint only.

        This ensures no context bleed — only checkpoint data survives.
        """
        self._items.clear()
        self.restore(checkpoint)

    @property
    def total_token_estimate(self) -> int:
        """Estimate total tokens across all items."""
        return sum(i["token_estimate"] for i in self._items)

    @property
    def item_count(self) -> int:
        return len(self._items)
