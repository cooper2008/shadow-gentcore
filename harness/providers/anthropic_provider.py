"""AnthropicProvider — wraps Anthropic SDK with thinking mode, tool use, streaming."""

from __future__ import annotations

from typing import Any, AsyncIterator

from harness.providers.base_provider import BaseProvider, LLMResponse, LLMChunk


class AnthropicProvider(BaseProvider):
    """Anthropic Claude provider.

    Wraps the Anthropic Python SDK for chat completions with support for:
    - Extended thinking mode
    - Tool use
    - Streaming responses

    Requires ANTHROPIC_API_KEY environment variable.
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = "claude-sonnet-4-6-20250414",
        max_tokens: int = 4096,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazy-initialize the Anthropic client."""
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self._api_key)
            except ImportError:
                raise ImportError(
                    "anthropic package not installed. Install with: pip install anthropic"
                )
        return self._client

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        """Send a chat completion to Anthropic Claude.

        When ``output_schema`` kwarg is provided and no ``tools`` are already set,
        injects a ``submit_output`` tool with the schema as its input_schema and
        forces the model to call it via ``tool_choice``. This guarantees the
        response is schema-compliant JSON extracted from the tool input block.
        """
        client = self._get_client()
        model = kwargs.pop("model", self._model)
        max_tokens = kwargs.pop("max_tokens", self._max_tokens)
        tools = kwargs.pop("tools", None)
        output_schema: dict[str, Any] | None = kwargs.pop("output_schema", None)

        # Separate system message from conversation
        system = ""
        chat_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system = msg.get("content", "")
            else:
                chat_messages.append(msg)

        create_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": chat_messages,
        }
        if system:
            create_kwargs["system"] = system

        # Structured output: use submit_output tool trick when schema given and no
        # existing tools (we don't want to override tool-calling in ReAct loop)
        structured_output_mode = output_schema is not None and tools is None
        if structured_output_mode:
            create_kwargs["tools"] = [{
                "name": "submit_output",
                "description": "Submit the final structured output matching the required schema.",
                "input_schema": output_schema,
            }]
            create_kwargs["tool_choice"] = {"type": "tool", "name": "submit_output"}
        elif tools:
            create_kwargs["tools"] = tools

        create_kwargs.update(kwargs)

        response = client.messages.create(**create_kwargs)

        # Parse response
        content = ""
        tool_calls = []
        for block in response.content:
            if hasattr(block, "text"):
                content += block.text
            elif hasattr(block, "type") and block.type == "tool_use":
                if structured_output_mode and block.name == "submit_output":
                    # Extract structured output as JSON string
                    import json
                    content = json.dumps(block.input)
                else:
                    tool_calls.append({
                        "id": block.id,
                        "name": block.name,
                        "arguments": block.input,
                    })

        return LLMResponse(
            content=content,
            tokens_used=response.usage.input_tokens + response.usage.output_tokens,
            tool_calls=tool_calls,
            model=model,
            stop_reason=response.stop_reason,
            raw={"id": response.id},
        )

    async def stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> AsyncIterator[LLMChunk]:
        """Stream a chat completion from Anthropic Claude."""
        client = self._get_client()
        model = kwargs.pop("model", self._model)
        max_tokens = kwargs.pop("max_tokens", self._max_tokens)

        system = ""
        chat_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system = msg.get("content", "")
            else:
                chat_messages.append(msg)

        create_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": chat_messages,
        }
        if system:
            create_kwargs["system"] = system
        create_kwargs.update(kwargs)

        with client.messages.stream(**create_kwargs) as stream:
            for text in stream.text_stream:
                yield LLMChunk(content=text, delta=text)
            yield LLMChunk(is_final=True)

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def default_model(self) -> str:
        return self._model
