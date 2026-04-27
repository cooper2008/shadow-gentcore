"""AnthropicProvider — wraps Anthropic SDK with thinking mode, tool use, streaming."""

from __future__ import annotations

from typing import Any, AsyncIterator

from harness.providers.base_provider import BaseProvider, LLMResponse, LLMChunk


def _is_transient_error(exc: Exception) -> bool:
    """True when ``exc`` looks like a transient API blip worth retrying.

    Detects: HTTP 5xx, 429 (rate limit), 408 (timeout), and BigModel-specific
    "网络错误" 4xx codes that GLM-5.1/MiniMax intermittently return mid-call.
    NOT transient: 401/403 auth errors, schema mismatches, malformed inputs,
    or any error message that names a permanent problem.
    """
    msg = str(exc)
    msg_lower = msg.lower()

    # Anthropic SDK exposes status_code on its APIError subclasses.
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        if status >= 500 or status == 429 or status == 408:
            return True
        # 401/403/404 are NOT transient — fail fast.
        if status in (401, 403, 404):
            return False
        # Other 4xx: only transient if the body says so (BigModel quirk).

    # Vendor-specific transient signals in error message body.
    transient_markers = (
        "网络错误",          # BigModel/GLM "network error"
        "请稍后重试",         # BigModel "please retry later"
        "timeout",
        "timed out",
        "connection reset",
        "connection refused",
        "rate limit",
        "ratelimit",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "internal server error",
        "temporarily unavailable",
        "overloaded",
    )
    if any(marker in msg_lower or marker in msg for marker in transient_markers):
        return True

    return False


class AnthropicProvider(BaseProvider):
    """Anthropic Claude provider.

    Wraps the Anthropic Python SDK for chat completions with support for:
    - Extended thinking mode
    - Tool use
    - Streaming responses

    Credentials (in precedence order):
      * explicit ``api_key`` / ``auth_token`` constructor args
      * ``ANTHROPIC_AUTH_TOKEN`` env var (bearer token — used by Minimax etc.)
      * ``ANTHROPIC_API_KEY`` env var (standard Anthropic x-api-key auth)

    Endpoint override:
      * explicit ``base_url`` constructor arg
      * ``ANTHROPIC_BASE_URL`` env var
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = "claude-sonnet-4-5-20250929",
        max_tokens: int = 4096,
        base_url: str | None = None,
        auth_token: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._base_url = base_url
        self._auth_token = auth_token
        # Timeout per LLM call. Resolution order:
        #   1. Explicit `timeout` constructor arg (wins)
        #   2. ANTHROPIC_TIMEOUT env var
        #   3. Per-model default from model_hints.get_model_timeout (GLM/
        #      MiniMax/Gemini get 900s; Claude/OpenAI get 300s)
        # Non-Anthropic-compat endpoints (GLM 5.1, MiniMax M2.7) often run
        # 5-12 min per call on long react loops; without a bounded timeout
        # the SDK waits forever and genesis silently exits.
        import os as _os
        from harness.providers.model_hints import get_model_timeout as _model_timeout
        if timeout is not None:
            self._timeout: float = float(timeout)
        elif _os.environ.get("ANTHROPIC_TIMEOUT"):
            self._timeout = float(_os.environ["ANTHROPIC_TIMEOUT"])
        else:
            self._timeout = _model_timeout(model)
        self._max_retries = max_retries if max_retries is not None else int(_os.environ.get("ANTHROPIC_MAX_RETRIES", "2"))
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazy-initialize the Anthropic client."""
        if self._client is None:
            try:
                import os
                import anthropic
                client_kwargs: dict[str, Any] = {
                    "timeout": self._timeout,
                    "max_retries": self._max_retries,
                }

                auth_token = self._auth_token or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                if auth_token:
                    client_kwargs["auth_token"] = auth_token
                elif self._api_key:
                    client_kwargs["api_key"] = self._api_key

                base_url = self._base_url or os.environ.get("ANTHROPIC_BASE_URL")
                if base_url:
                    client_kwargs["base_url"] = base_url

                self._client = anthropic.Anthropic(**client_kwargs)
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

        # Strip framework-internal metadata (underscore-prefixed keys) from
        # content blocks before handing to the Anthropic SDK. React mode may
        # attach `_thought_signature` to tool_use blocks for Gemini round-trip;
        # Anthropic's schema rejects unknown fields on ToolUseBlockParam.
        chat_messages = _strip_internal_keys(chat_messages)

        # Model-specific prompt nudge (opt-in — empty for strong models)
        from harness.providers.model_hints import get_model_hint
        _hint = get_model_hint(model)
        if _hint:
            system = (system or "") + _hint

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

        # Treat `tools=[]` the same as `tools=None`: when no real tools are
        # available, the model has nothing to coexist with, so submit_output
        # MUST be forced. Pre-fix, an empty list fell through to coexist mode
        # (tool_choice=auto) and weak-instruct vendors (GLM/MiniMax via
        # Anthropic-compat) reliably emitted plain `{}` content instead of
        # calling the only available tool — silently breaking single-shot
        # agents like AgentArchitect/v2, ConflictResolver, ContextEngineer.
        has_real_tools = bool(tools)
        forced_submit_output = output_schema is not None and not has_real_tools
        coexist_submit_output = output_schema is not None and has_real_tools

        if forced_submit_output:
            create_kwargs["tools"] = [submit_output_tool]
            create_kwargs["tool_choice"] = {"type": "tool", "name": "submit_output"}
        elif coexist_submit_output:
            create_kwargs["tools"] = [*tools, submit_output_tool]
            # Intentionally no tool_choice override — Anthropic defaults to "auto".
        elif tools:
            create_kwargs["tools"] = tools

        create_kwargs.update(kwargs)

        # Transient-error retry: BigModel-fronted vendors (GLM-5.1, MiniMax)
        # intermittently return 5xx and "network error" 4xx codes that succeed
        # on a fresh attempt seconds later. A single un-retried blip kills the
        # genesis pipeline (the failing step's gate hits max_retries with the
        # same blip each time). Three attempts with 1s/2s backoff covers the
        # observed transient window without hiding real failures (auth, etc.).
        import asyncio as _asyncio
        import logging as _logging
        _provider_log = _logging.getLogger(__name__)
        last_exc: Exception | None = None
        response = None
        for attempt in range(3):
            try:
                response = client.messages.create(**create_kwargs)
                break
            except Exception as exc:
                if not _is_transient_error(exc) or attempt == 2:
                    raise
                backoff = 1.0 * (2 ** attempt)  # 1s, 2s
                _provider_log.warning(
                    "anthropic.messages.create transient error (attempt %d/3): %s; "
                    "retrying in %.1fs",
                    attempt + 1, str(exc)[:200], backoff,
                )
                last_exc = exc
                await _asyncio.sleep(backoff)
        # Should be unreachable — the loop either breaks with `response` set
        # or re-raises the last exception. Defensive guard for type-checker.
        if response is None:
            raise last_exc or RuntimeError("anthropic.messages.create returned None")

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

        # MiniMax-compat fallback: MiniMax's Anthropic-compat endpoint emits
        # tool calls as `<minimax:tool_call><invoke name="..."><parameter ...>`
        # XML **inside text content** instead of proper Anthropic tool_use
        # content blocks. When we see that marker and no structured tool_calls
        # came back, extract and synthesize them. Non-MiniMax vendors never
        # emit this marker → zero-impact no-op.
        if not tool_calls and not submit_output_fired and "<minimax:tool_call>" in content:
            synth_calls, synth_submit_json = _parse_minimax_tool_calls(
                content, submit_output_enabled=forced_submit_output or coexist_submit_output,
            )
            if synth_submit_json is not None:
                content = synth_submit_json
                submit_output_fired = True
            elif synth_calls:
                tool_calls = synth_calls
                # Strip the XML from content so downstream sees a clean message
                import re as _re
                content = _re.sub(
                    r"<minimax:tool_call>[\s\S]*?</minimax:tool_call>\s*",
                    "", content,
                ).strip()

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


def _parse_minimax_tool_calls(
    content: str,
    submit_output_enabled: bool,
) -> tuple[list[dict[str, Any]], str | None]:
    """Extract MiniMax-style tool calls from response text.

    MiniMax's Anthropic-compat endpoint emits tool calls as text XML:

        <minimax:tool_call>
        <invoke name="TOOL_NAME">
        <parameter name="PARAM">VALUE</parameter>
        ...
        </invoke>
        </minimax:tool_call>

    VALUE is typically a JSON object/array serialized inline; sometimes a
    plain string. This helper parses the XML-ish blob and returns synthesized
    tool_call dicts compatible with the rest of the framework.

    Returns:
        (tool_calls, submit_output_json) — if the only invoke is
        `submit_output` AND submit_output_enabled is True, returns
        (empty list, merged-args JSON string) to mirror the native
        submit_output completion path. Otherwise returns the extracted
        tool_calls and None.
    """
    import re
    import json as _json

    results: list[dict[str, Any]] = []
    submit_args: dict[str, Any] | None = None

    for invoke_match in re.finditer(
        r'<invoke\s+name="([^"]+)">([\s\S]*?)</invoke>', content
    ):
        tool_name = invoke_match.group(1)
        body = invoke_match.group(2)
        args: dict[str, Any] = {}
        for param_match in re.finditer(
            r'<parameter\s+name="([^"]+)">([\s\S]*?)</parameter>', body
        ):
            key = param_match.group(1)
            raw = param_match.group(2).strip()
            # Try parsing as JSON first; fall back to raw string.
            try:
                args[key] = _json.loads(raw)
            except (ValueError, TypeError):
                args[key] = raw

        if submit_output_enabled and tool_name == "submit_output":
            submit_args = args
        else:
            results.append({
                "id": f"minimax_{len(results)}",
                "name": tool_name,
                "arguments": args,
            })

    if submit_args is not None:
        return [], _json.dumps(submit_args)
    return results, None


def _strip_internal_keys(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove underscore-prefixed keys from content blocks before sending.

    Framework-internal metadata (e.g. `_thought_signature` for Gemini round-trip)
    must not leak to the Anthropic SDK which validates against a strict schema.
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            out.append(msg)
            continue
        cleaned_blocks: list[Any] = []
        for block in content:
            if isinstance(block, dict):
                cleaned_blocks.append({k: v for k, v in block.items() if not k.startswith("_")})
            else:
                cleaned_blocks.append(block)
        out.append({**msg, "content": cleaned_blocks})
    return out
