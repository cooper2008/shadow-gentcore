"""OpenAIProvider — wraps OpenAI SDK with tool use and streaming."""

from __future__ import annotations

from typing import Any, AsyncIterator

from harness.providers.base_provider import BaseProvider, LLMResponse, LLMChunk


class OpenAIProvider(BaseProvider):
    """OpenAI GPT provider.

    Wraps the OpenAI Python SDK for chat completions with support for:
    - Function/tool calling
    - Streaming responses
    - JSON mode

    Requires OPENAI_API_KEY environment variable.
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = "gpt-4o",
        max_tokens: int = 4096,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazy-initialize the OpenAI client."""
        if self._client is None:
            try:
                import openai
                self._client = openai.OpenAI(api_key=self._api_key)
            except ImportError:
                raise ImportError(
                    "openai package not installed. Install with: pip install openai"
                )
        return self._client

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        """Send a chat completion to OpenAI."""
        client = self._get_client()
        model = kwargs.pop("model", self._model)
        max_tokens = kwargs.pop("max_tokens", self._max_tokens)
        tools = kwargs.pop("tools", None)

        create_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if tools:
            create_kwargs["tools"] = tools
        create_kwargs.update(kwargs)

        response = client.chat.completions.create(**create_kwargs)

        choice = response.choices[0]
        content = choice.message.content or ""

        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                import json
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": json.loads(tc.function.arguments),
                })

        return LLMResponse(
            content=content,
            tokens_used=response.usage.total_tokens if response.usage else 0,
            tool_calls=tool_calls,
            model=model,
            stop_reason=choice.finish_reason,
            raw={"id": response.id},
        )

    async def stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> AsyncIterator[LLMChunk]:
        """Stream a chat completion from OpenAI."""
        client = self._get_client()
        model = kwargs.pop("model", self._model)
        max_tokens = kwargs.pop("max_tokens", self._max_tokens)

        create_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "stream": True,
        }
        create_kwargs.update(kwargs)

        stream = client.chat.completions.create(**create_kwargs)
        for chunk in stream:
            if chunk.choices:
                delta = chunk.choices[0].delta
                text = delta.content or ""
                finish = chunk.choices[0].finish_reason
                yield LLMChunk(
                    content=text,
                    delta=text,
                    is_final=finish is not None,
                )
        yield LLMChunk(is_final=True)

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def default_model(self) -> str:
        return self._model
