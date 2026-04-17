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

        ``output_schema`` behaviour (H1 — always-on submit_output):

          * schema + no tools  → forced-submit_output mode. Inject
            ``submit_output`` as the only tool with ``tool_choice`` forcing it.
            The model MUST return schema-compliant JSON.
          * schema + tools     → coexist mode. Inject ``submit_output``
            alongside the agent's declared tools with ``tool_choice="auto"``.
            The model can keep using tools OR finalise via submit_output. When
            it calls submit_output, we convert that to structured ``content``
            and suppress the call from ``tool_calls`` so the strategy sees a
            clean completion signal (empty tool_calls + JSON content).
          * no schema          → passthrough (tools if given, else no tools).
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

        # submit_output tool definition — reused by both forced + coexist paths.
        submit_output_tool = {
            "name": "submit_output",
            "description": (
                "Submit the final structured output matching the required schema. "
                "Call this when you have gathered enough information to answer — "
                "it signals completion and returns your answer to the caller."
            ),
            "input_schema": output_schema or {"type": "object", "properties": {}},
        }

        forced_submit_output = output_schema is not None and tools is None
        coexist_submit_output = output_schema is not None and tools is not None

        if forced_submit_output:
            create_kwargs["tools"] = [submit_output_tool]
            create_kwargs["tool_choice"] = {"type": "tool", "name": "submit_output"}
        elif coexist_submit_output:
            create_kwargs["tools"] = [*tools, submit_output_tool]
            # Intentionally no tool_choice override — Anthropic defaults to "auto".
        elif tools:
            create_kwargs["tools"] = tools

        create_kwargs.update(kwargs)

        response = client.messages.create(**create_kwargs)

        # Parse response
        content = ""
        tool_calls: list[dict[str, Any]] = []
        submit_output_fired = False
        for block in response.content:
            if hasattr(block, "text"):
                content += block.text
            elif hasattr(block, "type") and block.type == "tool_use":
                if (forced_submit_output or coexist_submit_output) and block.name == "submit_output":
                    # Structured completion: overwrite content with JSON and
                    # drop any sibling tool_calls so the strategy sees a
                    # clean "done" signal.
                    import json
                    content = json.dumps(block.input)
                    submit_output_fired = True
                else:
                    tool_calls.append({
                        "id": block.id,
                        "name": block.name,
                        "arguments": block.input,
                    })

        if submit_output_fired:
            tool_calls = []

        return LLMResponse(
            content=content,
            tokens_used=response.usage.input_tokens + response.usage.output_tokens,
            tool_calls=tool_calls,
            model=model,
            stop_reason=response.stop_reason,
            raw={"id": response.id, "submit_output_fired": submit_output_fired},
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
