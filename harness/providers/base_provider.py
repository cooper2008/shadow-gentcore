"""BaseProvider — abstract LLM provider interface with response dataclasses."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class LLMResponse:
    """Structured response from an LLM provider."""
    content: str = ""
    tokens_used: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    stop_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMChunk:
    """A single chunk from a streaming LLM response."""
    content: str = ""
    delta: str = ""
    tool_call_delta: dict[str, Any] | None = None
    is_final: bool = False
    tokens_used: int = 0


class BaseProvider(ABC):
    """Abstract base class for LLM providers.

    Concrete implementations wrap specific SDKs (Anthropic, OpenAI, Bedrock).
    """

    @abstractmethod
    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            **kwargs: Provider-specific options (model, temperature, tools, etc.).

        Returns:
            LLMResponse with content, token usage, and optional tool calls.
        """

    @abstractmethod
    async def stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> AsyncIterator[LLMChunk]:
        """Send a streaming chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            **kwargs: Provider-specific options.

        Yields:
            LLMChunk objects as they arrive.
        """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider identifier (e.g., 'anthropic', 'openai')."""

    @property
    @abstractmethod
    def default_model(self) -> str:
        """Return the default model for this provider."""
