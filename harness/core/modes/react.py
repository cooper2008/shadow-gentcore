"""ReAct execution strategy — Think → Tool Call → Observe → Repeat."""

from __future__ import annotations

from typing import Any

from harness.core.modes.base import ExecutionStrategy, _resp_get
from harness.core.tool_disclosure import ToolDisclosureRouter


def _is_json_like(text: str) -> bool:
    """Return True if text looks like JSON (starts/ends with braces after stripping)."""
    t = text.strip()
    return t.startswith("{") and t.endswith("}")


def _satisfies_schema_shape(text: str, schema: dict[str, Any] | None) -> bool:
    """Return True if `text` parses to a dict that covers the schema's required fields.

    Empty `{}` is NOT a valid answer when the schema declares required
    fields — we want the react loop's schema-coerce retry to fire.
    `_is_json_like` alone accepts `{}` because it parses, which silently
    masked GLM/MiniMax failures where the model closes its turn with
    an empty object.
    """
    if not schema or not text:
        return False
    import json as _json
    try:
        parsed = _json.loads(text.strip())
    except (ValueError, TypeError):
        return False
    if not isinstance(parsed, dict):
        return False
    required = schema.get("required") or []
    if required and not any(k in parsed for k in required):
        return False
    return True


def _minimal_example_for_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    """Build a minimal example object that satisfies `schema`'s required fields.

    Used by schema-coerce retries to give weak-instruct vendors (GLM, MiniMax)
    a concrete shape they can copy from instead of just describing the schema.
    Vendors observed emitting `submit_output({})` after tool_choice forcing —
    a valid tool call with empty args. Showing them a concrete example
    bypasses that failure mode.

    Per-type defaults are minimal-but-valid:
      - object       → recurse into required properties
      - array        → empty list (or single example item if items.required)
      - string       → "..."
      - integer/number → 0
      - boolean      → false
      - enum         → first enum value
    """
    if not isinstance(schema, dict):
        return {}
    schema_type = schema.get("type")
    if schema_type == "object":
        out: dict[str, Any] = {}
        props = schema.get("properties") or {}
        for key in schema.get("required") or []:
            sub = props.get(key, {"type": "string"})
            out[key] = _minimal_example_for_value(sub)
        return out
    return {}


def _minimal_example_for_value(schema: dict[str, Any]) -> Any:
    """Sibling of `_minimal_example_for_schema` for non-root values."""
    if not isinstance(schema, dict):
        return None
    enum = schema.get("enum")
    if enum:
        return enum[0]
    schema_type = schema.get("type")
    if schema_type == "object":
        return _minimal_example_for_schema(schema)
    if schema_type == "array":
        items = schema.get("items") or {}
        if isinstance(items, dict) and items.get("required"):
            # Show one example item so the model knows the shape.
            return [_minimal_example_for_value(items)]
        return []
    if schema_type == "string":
        return "..."
    if schema_type in ("integer", "number"):
        return 0
    if schema_type == "boolean":
        return False
    return None


def _estimate_message_tokens(messages: list[dict[str, Any]]) -> int:
    """Cheap chars/4 estimate of total tokens across a message list.

    Mirrors the heuristic used in ContextEngine — accurate enough to drive
    a compaction trigger without pulling in a tokenizer dependency.
    """
    total = 0
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, list):
            for block in c:
                if not isinstance(block, dict):
                    continue
                txt = block.get("text") or block.get("content") or ""
                inp = block.get("input")
                total += len(str(txt)) // 4
                if inp:
                    total += len(str(inp)) // 4
        else:
            total += len(str(c)) // 4
    return total


def _serialize_messages_for_summary(messages: list[dict[str, Any]]) -> str:
    """Render a message list as plain text the summarizer can read.

    Tool results are truncated per-block to keep the summary prompt itself
    bounded — the whole point is that this slice is too big to ship raw.
    """
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    lines.append(f"[{role}:text] {str(block.get('text', ''))[:1500]}")
                elif btype == "tool_use":
                    args = block.get("input", {})
                    arg_keys = list(args.keys()) if isinstance(args, dict) else str(args)[:200]
                    lines.append(f"[{role}:tool_use] {block.get('name', '?')}({arg_keys})")
                elif btype == "tool_result":
                    out = str(block.get("content", ""))[:600]
                    lines.append(f"[{role}:tool_result] {out}")
        else:
            lines.append(f"[{role}] {str(content)[:1500]}")
    return "\n".join(lines)


async def _compact_message_history(
    messages: list[dict[str, Any]],
    provider: Any,
    keep_last_n_rounds: int,
    strategy: str,
) -> list[dict[str, Any]]:
    """Compact ``messages`` in place-of, returning a new list.

    Layout assumed (matches ReActStrategy below):
      [0]: user (initial task)
      [1]: assistant (step 0 — text + tool_use)
      [2]: user (step 0 — tool_result)
      [3]: assistant (step 1)
      [4]: user (step 1 — tool_result)
      ...

    A "round" is one (assistant, tool_result) pair = 2 messages. We keep the
    head (initial task) and the last ``keep_last_n_rounds`` rounds intact;
    everything between is summarized into a single synthetic user message
    that's prepended with the original task. The summary message replaces
    head + middle so we don't end up with two consecutive user roles.

    Strategy:
      - 'summarize_oldest': call provider.chat with a brief summarization prompt.
      - 'drop_oldest':       drop the middle entirely with a "[N steps elided]" marker.
      - 'none':              return messages unchanged.

    Returns ``messages`` unchanged when there's nothing to compact or when
    summarization fails (fail-open keeps the loop running).
    """
    if strategy == "none":
        return messages
    keep_msgs = 2 * keep_last_n_rounds
    if len(messages) < keep_msgs + 3:
        return messages
    head_msg = messages[0]
    tail = messages[-keep_msgs:]
    middle = messages[1:-keep_msgs]
    if not middle:
        return messages

    head_content = head_msg.get("content", "")
    if isinstance(head_content, list):
        head_content = "\n".join(
            str(b.get("text", "")) for b in head_content if isinstance(b, dict)
        )

    if strategy == "drop_oldest":
        synthetic = {
            "role": "user",
            "content": (
                f"{head_content}\n\n"
                f"[Auto-compacted: {len(middle)} prior messages elided to fit context budget. "
                "Continue from your last observation.]"
            ),
        }
        return [synthetic] + tail

    middle_text = _serialize_messages_for_summary(middle)
    summary_prompt = [{
        "role": "user",
        "content": (
            "You are a conversation compactor. Below is the message history of an AI "
            "agent's tool-using execution. Produce a concise summary (≤300 words) that "
            "preserves: (1) tool calls made and their KEY findings, (2) decisions and "
            "facts established, (3) any open questions. Skip raw tool output verbatim — "
            "extract the signal.\n\n---\n"
            f"{middle_text}\n---"
        ),
    }]
    try:
        resp = await provider.chat(summary_prompt)
    except Exception:
        return messages
    summary_text = str(_resp_get(resp, "content", "")).strip()
    if not summary_text:
        return messages
    synthetic = {
        "role": "user",
        "content": (
            f"{head_content}\n\n"
            f"[Auto-compacted summary of {len(middle)} prior messages — "
            "context budget hit, full history elided]\n"
            f"{summary_text}\n\n"
            "Continue your work from your last observation."
        ),
    }
    return [synthetic] + tail


def _build_anthropic_tools(
    tool_executor: Any,
    allowed: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Build Anthropic-format tool definitions from registered adapters.

    Returns a list of tool dicts that can be passed to provider.chat(tools=...).
    This enables the LLM to return structured tool_use blocks instead of text.

    Args:
        tool_executor: ToolExecutor with registered adapters.
        allowed: Whitelist of tool names from the agent manifest. When provided,
                 only tools in this list are returned — this is the Phase 1
                 manifest-whitelist filter that prevents all 129 registered tools
                 from being sent to the LLM on every call.
                 When None or empty, falls back to all registered adapters
                 (backward-compatible behaviour for callers without a manifest).
    """
    if tool_executor is None:
        return []
    adapters = getattr(tool_executor, "_adapters", {})
    if not adapters:
        return []

    # Apply manifest whitelist — only expose tools the agent declared.
    # `allowed` may be plain strings or dicts {name: ...} from manifest tool entries.
    # IMPORTANT: an empty list (`tools: []`) means "no tools" — distinct from
    # `None` ("no whitelist filter, expose every registered adapter"). Pre-fix
    # used `if allowed:` which collapsed both into the no-filter branch, so
    # single-shot agents like AgentArchitect/v2, ConflictResolver, and
    # ContextEngineer (which declare `tools: []` by design) silently received
    # ALL 100+ registered builtins. Weak-instruct vendors (GLM-5.1) then
    # picked a wrong tool, got no useful result, and gave up emitting `{}`.
    if allowed is not None:
        allowed_names: set[str] = {
            a["name"] if isinstance(a, dict) else str(a)
            for a in allowed
        }
        names_to_build = [n for n in allowed_names if n in adapters]
    else:
        names_to_build = list(adapters.keys())

    if not names_to_build:
        return []

    # Build tool definitions for common tools
    tool_schemas: dict[str, dict[str, Any]] = {
        "file_read": {
            "description": "Read the contents of a file at the given path",
            "input_schema": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string", "description": "File path to read"}}},
        },
        "file_write": {
            "description": "Write content to a file at the given path (creates directories as needed)",
            "input_schema": {"type": "object", "required": ["path", "content"], "properties": {"path": {"type": "string"}, "content": {"type": "string"}}},
        },
        "list_dir": {
            "description": "List directory contents at the given path",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string", "description": "Directory path (default: current dir)", "default": "."}}},
        },
        "search_code": {
            "description": "Search for a pattern in source files (grep -rn)",
            "input_schema": {"type": "object", "required": ["pattern"], "properties": {"pattern": {"type": "string"}, "path": {"type": "string", "default": "."}}},
        },
        "search_files": {
            "description": "Find files by name pattern",
            "input_schema": {"type": "object", "properties": {"pattern": {"type": "string", "default": "*"}, "path": {"type": "string", "default": "."}}},
        },
        "shell_exec": {
            "description": "Execute a shell command and return stdout/stderr",
            "input_schema": {"type": "object", "required": ["command"], "properties": {"command": {"type": "string"}}},
        },
        "file_list": {
            "description": "List files in a directory (ls -la)",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string", "default": "."}}},
        },
        "list_paths": {
            "description": (
                "Tier 1.5 — browse the project file tree without reading file "
                "content. Use when the preloaded file-tree map was truncated "
                "or when you need to discover paths before calling "
                "`origin_fetch`. Scope-guarded to domain_root. Pattern is a "
                "glob like '*.py'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "prefix": {"type": "string", "description": "Path prefix (relative to domain root). Default: domain root itself."},
                    "depth": {"type": "integer", "default": 2, "description": "How many levels to recurse. Max 5."},
                    "pattern": {"type": "string", "description": "Optional glob filter for filenames (e.g. '*.py')."},
                    "max_entries": {"type": "integer", "default": 200, "description": "Cap on returned entries. Max 500."},
                },
            },
        },
        "context_retrieve": {
            "description": (
                "Tier 2 — retrieve the top-K most relevant reference chunks for a topic. "
                "Use this BEFORE giving up on a question: it's cheaper than loading a whole "
                "reference file and often contains exactly the snippet you need. If it returns "
                "no matches, consider `origin_fetch` (Tier 3) to pull live from the source."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic phrase — matches chunk topic lines."},
                    "keywords": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Additional retrieval keywords (nouns / API names / patterns).",
                    },
                    "top_k": {"type": "integer", "default": 3, "description": "Max chunks to return."},
                    "min_score": {"type": "number", "default": 0.5, "description": "Floor score for inclusion."},
                },
            },
        },
        "origin_fetch": {
            "description": (
                "Tier 3 — read a file directly from the origin source (e.g. a GitHub repo) "
                "via the SourceAdapter cache. Use this only after context_retrieve returns "
                "nothing useful — it costs more (network + log entry) but closes the "
                "'answer lives outside my snapshot' gap. Scope-guarded: only paths matching "
                "the agent's declared origin_fallback.scope glob are allowed."
            ),
            "input_schema": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string", "description": "Relative path within the source (e.g. src/auth/session.py)."},
                    "source_uri": {"type": "string", "description": "Optional explicit source URI (overrides agent default)."},
                    "max_bytes": {"type": "integer", "default": 20000, "description": "Truncate returned file to this size."},
                },
            },
        },
        "memory_recall": {
            "description": (
                "Tier 4 — query this agent's persistent memory for past task outputs. "
                "Use when the current task looks similar to something you've solved before — "
                "past solutions may shortcut the work. AgentRunner records `run_output` "
                "entries automatically; callers can filter by key if specific memories are "
                "expected. Returns entries newest-first."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Optional key filter (e.g. \"run_output\"). Omit for all keys."},
                    "k": {"type": "integer", "default": 5, "description": "Max entries to return."},
                },
            },
        },
    }

    tools = []
    for name in names_to_build:
        schema = tool_schemas.get(name)
        if schema:
            tools.append({"name": name, **schema})
        else:
            # Generic fallback for unknown/domain-specific tools
            tools.append({
                "name": name,
                "description": f"Execute the {name} tool",
                "input_schema": {"type": "object", "properties": {}},
            })
    return tools


class ReActStrategy(ExecutionStrategy):
    """ReAct (Reasoning + Acting) execution loop.

    Each iteration:
    1. Send messages to LLM (with tool definitions)
    2. If LLM returns tool_use blocks, execute them via ToolExecutor
    3. Append tool results as observations
    4. Repeat until LLM produces final answer or max_steps reached
    """

    def __init__(
        self,
        max_steps: int = 10,
        compaction: dict[str, Any] | Any | None = None,
        **kwargs: Any,
    ) -> None:
        self.max_steps = max_steps
        # Compaction may arrive as a dict (model_dump'd from manifest) or a
        # CompactionConfig pydantic model. Normalize to plain attrs so we
        # don't depend on agent_contracts import here.
        if compaction is None:
            self._compaction_strategy = "summarize_oldest"
            self._compaction_keep_last = 2
            self._compaction_trigger: int | None = None
        elif isinstance(compaction, dict):
            self._compaction_strategy = compaction.get("strategy", "summarize_oldest")
            self._compaction_keep_last = int(compaction.get("keep_last_n_turns", 2))
            trig = compaction.get("trigger_token_estimate")
            self._compaction_trigger = int(trig) if trig else None
        else:
            self._compaction_strategy = getattr(compaction, "strategy", "summarize_oldest")
            self._compaction_keep_last = int(getattr(compaction, "keep_last_n_turns", 2))
            trig = getattr(compaction, "trigger_token_estimate", None)
            self._compaction_trigger = int(trig) if trig else None

    @property
    def name(self) -> str:
        return "react"

    async def execute(
        self,
        messages: list[dict[str, Any]],
        provider: Any,
        tool_executor: Any | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        output_schema = kwargs.get("output_schema")
        # Default to None (not []) so caller-omitted means "no whitelist
        # filter — expose all builtins". Caller-explicit `[]` still means
        # "agent declared no tools" and is honoured. The two cases must
        # stay distinct after the empty-list whitelist fix.
        declared_tools: list[Any] | None = kwargs.get("declared_tools", None)
        steps: list[dict[str, Any]] = []
        current_messages = list(messages)
        total_tokens = 0

        # Build progressive disclosure router when tool_executor is available.
        # If any declared tool has level=L1, the router manages promotion.
        # Otherwise (all L2 or no declared tools) fall back to flat whitelist.
        router: ToolDisclosureRouter | None = None
        if tool_executor is not None and declared_tools:
            # Router needs dict entries to read level; string entries default to L2
            router = ToolDisclosureRouter(declared_tools, tool_executor)

        # Compute the initial tool set
        if router is not None:
            api_tools = router.current_api_tools()
        else:
            # Phase 1 flat whitelist — _build_anthropic_tools handles str/dict entries
            # IMPORTANT: pass declared_tools as-is (don't `or None`-collapse).
            # `[]` means the agent declared NO tools — distinct from `None`
            # ("no whitelist filter"). Pre-fix `declared_tools or None`
            # turned an empty list into None, exposing every registered
            # builtin to single-shot agents that asked for none.
            api_tools = _build_anthropic_tools(tool_executor, allowed=declared_tools)

        for step_num in range(self.max_steps):
            # Pass tools to provider so LLM returns structured tool_use blocks
            # On the last allowed step, omit tools so LLM is forced to produce text
            is_last_step = step_num == self.max_steps - 1
            chat_kwargs: dict[str, Any] = {}

            # Refresh tool set from router (picks up any promotions from prior step)
            if router is not None:
                api_tools = router.current_api_tools()

            if api_tools and not is_last_step:
                chat_kwargs["tools"] = api_tools

            # H1 — always pass output_schema when set. Provider injects
            # submit_output alongside declared tools (coexist mode) so the
            # LLM can signal completion with structured content at any step.
            if output_schema:
                chat_kwargs["output_schema"] = output_schema

            # Inject L1 hint as a system note on the first step only
            step_messages = current_messages
            if step_num == 0 and router is not None and router.has_l1_tools:
                l1_hint = router.l1_summary()
                if l1_hint:
                    # Append hint to the last user message so LLM sees available tools
                    step_messages = list(current_messages)
                    if step_messages and step_messages[-1].get("role") == "user":
                        prev = step_messages[-1]["content"]
                        step_messages[-1] = {
                            "role": "user",
                            "content": f"{prev}\n\n[Tool hints]\n{l1_hint}",
                        }

            response = await provider.chat(step_messages, **chat_kwargs)
            total_tokens += _resp_get(response, "tokens_used", 0)

            tool_calls = _resp_get(response, "tool_calls", [])
            content = _resp_get(response, "content", "")

            # Promote L1 tools mentioned in this response
            if router is not None and router.pending_l1_count > 0:
                probe = content + " ".join(tc.get("name", "") for tc in tool_calls)
                router.detect_and_promote(probe)

            steps.append({
                "step": step_num + 1,
                "type": "think" if not tool_calls else "act",
                "content": content,
                "tool_calls": tool_calls,
            })

            if not tool_calls:
                # No tool calls = final answer; re-call with schema if needed.
                # Trigger schema-coerce when content either isn't JSON-shaped
                # OR parses to an empty/shape-deficient dict (e.g. `{}` or
                # one missing all of the declared `required` fields). The
                # latter covers the observed GLM/MiniMax failure where the
                # model closes its turn with literal `{}` after tool calls.
                needs_coerce = (
                    output_schema
                    and (not _is_json_like(content) or not _satisfies_schema_shape(content, output_schema))
                )
                if needs_coerce:
                    import json as _json
                    import logging as _logging
                    _coerce_log = _logging.getLogger(__name__)
                    # Multi-attempt schema-coerce: weak-instruct vendors
                    # (GLM-5.1, MiniMax) commonly fire submit_output({}) on
                    # the first coerce — a valid tool call with empty args.
                    # Progressive escalation: schema → schema+missing-list →
                    # schema+missing-list+concrete-example. Stops as soon as
                    # the response satisfies required-field shape.
                    required = (output_schema or {}).get("required") or []
                    example = _minimal_example_for_schema(output_schema)
                    coerce_prompts = [
                        # Attempt 1: same as legacy — gentle restate
                        (
                            "Reformat your answer as JSON.\n\n"
                            f"Your output MUST be a JSON object matching this schema:\n"
                            f"```json\n{_json.dumps(output_schema, indent=2)}\n```\n"
                            "Output JSON only."
                        ),
                        # Attempt 2: explicit missing-fields callout
                        (
                            "Your previous response was empty or missing required "
                            f"fields. The schema requires ALL of: {required}. "
                            "You MUST populate every required field with realistic "
                            "values inferred from the conversation context. "
                            "Do NOT return an empty object. "
                            f"Schema:\n```json\n{_json.dumps(output_schema, indent=2)}\n```\n"
                            "Reply with the JSON object only — no prose, no fences."
                        ),
                        # Attempt 3: concrete copy-from example + last-chance
                        (
                            "FINAL ATTEMPT. Your previous responses were empty.\n"
                            "Here is a CONCRETE EXAMPLE matching the required shape:\n"
                            f"```json\n{_json.dumps(example, indent=2)}\n```\n"
                            "Replace the placeholder values with realistic values "
                            "synthesized from the prior conversation. Every required "
                            f"field ({required}) must be present and non-empty. "
                            "Output the JSON object only."
                        ),
                    ]
                    coerce_attempts = 0
                    coerce_api_errors = 0
                    coerce_succeeded = False
                    current_messages.append({"role": "assistant", "content": content})
                    for prompt in coerce_prompts:
                        coerce_attempts += 1
                        current_messages.append({"role": "user", "content": prompt})
                        try:
                            schema_response = await provider.chat(
                                current_messages, output_schema=output_schema
                            )
                            new_content = _resp_get(schema_response, "content", "")
                            total_tokens += _resp_get(schema_response, "tokens_used", 0)
                            steps.append({
                                "step": step_num + 1 + coerce_attempts,
                                "type": "schema_coerce",
                                "attempt": coerce_attempts,
                                "content": new_content,
                            })
                            if _satisfies_schema_shape(new_content, output_schema):
                                content = new_content
                                coerce_succeeded = True
                                break
                            # Persist assistant turn so retry sees it
                            current_messages.append({"role": "assistant", "content": new_content})
                            content = new_content  # carry forward for return
                        except Exception as _exc:
                            # Transient API errors (GLM/MiniMax via BigModel
                            # occasionally return 400/500 mid-coerce) must NOT
                            # abort the escalation — each prompt is independent
                            # and the next stronger prompt may still succeed.
                            # Pop the just-appended user message so the message
                            # list stays consistent for subsequent attempts.
                            coerce_api_errors += 1
                            if current_messages and current_messages[-1].get("role") == "user":
                                current_messages.pop()
                            _coerce_log.warning(
                                "schema_coerce attempt %d hit transient error: %s",
                                coerce_attempts, _exc,
                            )
                            steps.append({
                                "step": step_num + 1 + coerce_attempts,
                                "type": "schema_coerce_error",
                                "attempt": coerce_attempts,
                                "error": str(_exc)[:300],
                            })
                            continue
                    if coerce_succeeded:
                        _coerce_log.info(
                            "schema_coerce recovered empty/invalid output on attempt %d "
                            "(api_errors=%d)",
                            coerce_attempts, coerce_api_errors,
                        )
                    else:
                        _coerce_log.warning(
                            "schema_coerce exhausted %d attempts (api_errors=%d); "
                            "final content[:200]=%r",
                            coerce_attempts, coerce_api_errors, (content or "")[:200],
                        )
                result: dict[str, Any] = {
                    "content": content,
                    "tool_calls": [],
                    "tokens_used": total_tokens,
                    "steps": steps,
                }
                if router is not None:
                    result["tool_promotion_log"] = router.promotion_log()
                return result

            # Execute tool calls and collect observations
            # Build assistant message with tool_use blocks (Anthropic format)
            assistant_content: list[dict[str, Any]] = []
            if content:
                assistant_content.append({"type": "text", "text": content})
            for tc in tool_calls:
                block: dict[str, Any] = {
                    "type": "tool_use",
                    "id": tc.get("id", f"tool_{step_num}"),
                    "name": tc.get("name", ""),
                    "input": tc.get("arguments", tc.get("input", {})),
                }
                # Carry Gemini 3.x thought_signature across turns so the
                # OpenAIProvider can echo it back. Non-Gemini tool_calls
                # never set this → block stays identical to before.
                sig = tc.get("_thought_signature")
                if sig:
                    block["_thought_signature"] = sig
                assistant_content.append(block)
            current_messages.append({"role": "assistant", "content": assistant_content})

            # Execute tools and build tool_result message (Anthropic format)
            tool_results: list[dict[str, Any]] = []
            if tool_executor is not None:
                for tc in tool_calls:
                    result = await tool_executor.execute(tc)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc.get("id", f"tool_{step_num}"),
                        "content": str(result.get("output", "")),
                    })
                    steps.append({
                        "step": step_num + 1,
                        "type": "observe",
                        "tool_call_id": tc.get("id", ""),
                        "output": result.get("output", ""),
                    })
            current_messages.append({"role": "user", "content": tool_results})

            # Mid-run compaction — only fire when explicitly enabled (trigger
            # set on the manifest's execution_mode.compaction). Default trigger
            # is None → no behavior change for existing agents. We check after
            # appending observations so the budget reflects what the NEXT step
            # would have to send.
            if (
                self._compaction_trigger
                and step_num + 1 < self.max_steps
                and self._compaction_strategy != "none"
            ):
                est = _estimate_message_tokens(current_messages)
                if est > self._compaction_trigger:
                    before = len(current_messages)
                    current_messages = await _compact_message_history(
                        current_messages,
                        provider=provider,
                        keep_last_n_rounds=self._compaction_keep_last,
                        strategy=self._compaction_strategy,
                    )
                    after = len(current_messages)
                    if after < before:
                        steps.append({
                            "step": step_num + 1,
                            "type": "compaction",
                            "strategy": self._compaction_strategy,
                            "messages_before": before,
                            "messages_after": after,
                            "estimated_tokens_before": est,
                            "estimated_tokens_after": _estimate_message_tokens(current_messages),
                        })

        # Max steps reached — force one final LLM call WITHOUT tools to get summary
        if output_schema:
            import json
            schema_hint = (
                f"\n\nYour output MUST be a JSON object matching this schema:\n"
                f"```json\n{json.dumps(output_schema, indent=2)}\n```"
            )
        else:
            schema_hint = ""
        current_messages.append({
            "role": "user",
            "content": (
                "You have reached the maximum number of tool calls. STOP calling tools now. "
                "Produce your final structured output based on everything you have gathered so far. "
                f"Output JSON only.{schema_hint}"
            ),
        })
        try:
            final_kwargs: dict[str, Any] = {}
            if output_schema:
                final_kwargs["output_schema"] = output_schema
            final_response = await provider.chat(current_messages, **final_kwargs)  # No tools param = no tool_use
            final_content = _resp_get(final_response, "content", "")
            total_tokens += _resp_get(final_response, "tokens_used", 0)
            steps.append({"step": self.max_steps + 1, "type": "final_summary", "content": final_content})
        except Exception:
            final_content = steps[-1].get("content", "") if steps else ""

        truncated_result: dict[str, Any] = {
            "content": final_content,
            "tool_calls": [],
            "tokens_used": total_tokens,
            "steps": steps,
            "truncated": True,
        }
        if router is not None:
            truncated_result["tool_promotion_log"] = router.promotion_log()
        return truncated_result
