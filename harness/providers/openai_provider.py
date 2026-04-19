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
        model: str = "gpt-5.4",
        max_tokens: int = 4096,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._base_url = base_url
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazy-initialize the OpenAI client.

        Credentials resolve in this order:
          explicit api_key → OPENAI_API_KEY env var → GEMINI_API_KEY env var
          (Gemini's OpenAI-compat endpoint accepts either).

        Endpoint override: explicit base_url → OPENAI_BASE_URL env var. Set
        to Gemini's OpenAI-compat URL to route there:
          https://generativelanguage.googleapis.com/v1beta/openai/
        """
        if self._client is None:
            try:
                import os
                import openai
                client_kwargs: dict[str, Any] = {}

                api_key = (
                    self._api_key
                    or os.environ.get("OPENAI_API_KEY")
                    or os.environ.get("GEMINI_API_KEY")
                    or ""
                )
                if api_key:
                    client_kwargs["api_key"] = api_key

                base_url = self._base_url or os.environ.get("OPENAI_BASE_URL")
                if base_url:
                    client_kwargs["base_url"] = base_url

                self._client = openai.OpenAI(**client_kwargs)
            except ImportError:
                raise ImportError(
                    "openai package not installed. Install with: pip install openai"
                )
        return self._client

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        """Send a chat completion to OpenAI (or OpenAI-compatible endpoint).

        When ``output_schema`` is supplied, switches to JSON mode so the
        response is guaranteed to be a valid JSON object the framework's
        validator + gate expressions can read. Works with OpenAI native and
        OpenAI-compat endpoints (Gemini, DeepSeek, etc.).
        """
        client = self._get_client()
        model = kwargs.pop("model", self._model)
        max_tokens = kwargs.pop("max_tokens", self._max_tokens)
        tools = kwargs.pop("tools", None)
        output_schema: dict[str, Any] | None = kwargs.pop("output_schema", None)

        # Translate Anthropic-format tool blocks (tool_use / tool_result) in
        # message history to OpenAI-format (tool_calls / role=tool). React
        # strategy builds messages in Anthropic shape; Gemini's OpenAI-compat
        # rejects `tool_use` as an unknown content-part type.
        messages = self._translate_messages(messages)

        # Model-specific prompt nudge (opt-in — empty for strong models)
        from harness.providers.model_hints import get_model_hint
        _hint = get_model_hint(model)
        if _hint:
            messages = list(messages)
            if messages and messages[0].get("role") == "system":
                messages[0] = {
                    **messages[0],
                    "content": (messages[0].get("content", "") or "") + _hint,
                }
            else:
                messages = [{"role": "system", "content": _hint.strip()}, *messages]

        # Inject schema into system prompt so the model knows the shape
        if output_schema and messages:
            import json as _json
            schema_hint = (
                "\n\nYour final response MUST be a single JSON object matching this schema:\n"
                f"```json\n{_json.dumps(output_schema, indent=2)}\n```\n"
                "Do not wrap it in markdown fences. Emit raw JSON."
            )
            messages = list(messages)
            if messages[0].get("role") == "system":
                messages[0] = {
                    **messages[0],
                    "content": (messages[0].get("content", "") or "") + schema_hint,
                }
            else:
                messages = [{"role": "system", "content": schema_hint.strip()}, *messages]

        # Translate Anthropic-format tool schemas (react/plan_execute build
        # these) into OpenAI-format function schemas. A heuristic: if any tool
        # entry has `input_schema` or is missing the `type`+`function` wrapper,
        # it's Anthropic-shaped.
        def _to_openai_tool(t: dict[str, Any]) -> dict[str, Any]:
            if isinstance(t, dict) and t.get("type") == "function" and "function" in t:
                return t  # already OpenAI shape
            if not isinstance(t, dict):
                return t
            return {
                "type": "function",
                "function": {
                    "name": t.get("name", "unnamed_tool"),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema") or t.get("parameters") or {
                        "type": "object", "properties": {},
                    },
                },
            }

        openai_tools = [_to_openai_tool(t) for t in tools] if tools else None

        create_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if openai_tools:
            create_kwargs["tools"] = openai_tools

        # Force JSON mode whenever a schema is declared — BUT ONLY when
        # no tools are declared. Gemini's OpenAI-compat endpoint rejects
        # combining function calling (tools) with JSON response format:
        #   "Function calling with a response mime type: 'application/json'
        #    is unsupported" (HTTP 400 INVALID_ARGUMENT).
        # OpenAI native permits the combination, but the framework runs
        # the same code against Gemini-compat so the conservative path
        # is to pick one or the other. When tools are present, the model
        # can use tool_calls to emit structured output; system prompt
        # hints (see model_hints.py) ensure final-reply JSON quality.
        if output_schema and not openai_tools:
            create_kwargs["response_format"] = {"type": "json_object"}

        create_kwargs.update(kwargs)

        response = client.chat.completions.create(**create_kwargs)

        choice = response.choices[0]
        content = choice.message.content or ""

        tool_calls = []
        if choice.message.tool_calls:
            import json
            # Gemini 3.x thinking models emit `thought_signature` on every
            # tool-use response. The signature must be echoed back on the
            # follow-up turn or the API rejects with HTTP 400 "Function call
            # is missing a thought_signature in functionCall parts". Signature
            # may appear at message-level (extra_content.google.thought_signature)
            # or per-tool-call. We capture whichever is present; non-Gemini
            # vendors never populate this → `sig` stays None → no-op.
            msg_extra = getattr(choice.message, "extra_content", None) or {}
            msg_sig = None
            if isinstance(msg_extra, dict):
                msg_sig = (msg_extra.get("google") or {}).get("thought_signature")
            for tc in choice.message.tool_calls:
                tc_extra = getattr(tc, "extra_content", None) or {}
                tc_sig = None
                if isinstance(tc_extra, dict):
                    tc_sig = (tc_extra.get("google") or {}).get("thought_signature")
                sig = tc_sig or msg_sig
                entry: dict[str, Any] = {
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": json.loads(tc.function.arguments),
                }
                if sig:
                    entry["_thought_signature"] = sig
                tool_calls.append(entry)

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

    @staticmethod
    def _translate_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Translate Anthropic-format tool blocks into OpenAI-format tool calls.

        - Assistant message whose content is a list containing {type:"tool_use"}
          blocks becomes {role:"assistant", content:<text>, tool_calls:[...]}.
        - User message with {type:"tool_result"} blocks becomes a series of
          {role:"tool", tool_call_id:..., content:...} messages.
        """
        import json as _json
        out: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content")

            if role == "assistant" and isinstance(content, list):
                text_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                for block in content:
                    if not isinstance(block, dict):
                        text_parts.append(str(block))
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(block.get("text", ""))
                    elif btype == "tool_use":
                        tc_out: dict[str, Any] = {
                            "id": block.get("id", "call_0"),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": _json.dumps(block.get("input", {})),
                            },
                        }
                        # Echo Gemini 3.x thought_signature back on follow-up
                        # turn. No-op for other vendors (field absent).
                        sig = block.get("_thought_signature")
                        if sig:
                            tc_out["extra_content"] = {
                                "google": {"thought_signature": sig}
                            }
                        tool_calls.append(tc_out)
                new_msg: dict[str, Any] = {"role": "assistant"}
                joined = "\n".join(p for p in text_parts if p)
                if joined:
                    new_msg["content"] = joined
                elif not tool_calls:
                    new_msg["content"] = ""
                if tool_calls:
                    new_msg["tool_calls"] = tool_calls
                out.append(new_msg)
                continue

            if role == "user" and isinstance(content, list):
                tool_result_msgs: list[dict[str, Any]] = []
                leftover_text: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        leftover_text.append(str(block))
                        continue
                    btype = block.get("type")
                    if btype == "tool_result":
                        inner = block.get("content")
                        if isinstance(inner, list):
                            inner_text = "\n".join(
                                b.get("text", "") if isinstance(b, dict) else str(b)
                                for b in inner
                            )
                        else:
                            inner_text = str(inner) if inner is not None else ""
                        tool_result_msgs.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", "call_0"),
                            "content": inner_text,
                        })
                    elif btype == "text":
                        leftover_text.append(block.get("text", ""))
                    else:
                        leftover_text.append(str(block))
                if leftover_text:
                    out.append({"role": "user", "content": "\n".join(leftover_text)})
                out.extend(tool_result_msgs)
                continue

            # Default passthrough — string content or other roles
            out.append(dict(msg))

        return out

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def default_model(self) -> str:
        return self._model
