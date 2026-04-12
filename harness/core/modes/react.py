"""ReAct execution strategy — Think → Tool Call → Observe → Repeat."""

from __future__ import annotations

from typing import Any

from harness.core.modes.base import ExecutionStrategy, _resp_get
from harness.core.tool_disclosure import ToolDisclosureRouter


def _is_json_like(text: str) -> bool:
    """Return True if text looks like JSON (starts/ends with braces after stripping)."""
    t = text.strip()
    return t.startswith("{") and t.endswith("}")


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
    if allowed:
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

    def __init__(self, max_steps: int = 10, **kwargs: Any) -> None:
        self.max_steps = max_steps

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
        declared_tools: list[Any] = kwargs.get("declared_tools", [])
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
            api_tools = _build_anthropic_tools(tool_executor, allowed=declared_tools or None)

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
                # No tool calls = final answer; re-call with schema if needed
                if output_schema and not _is_json_like(content):
                    import json
                    schema_hint = (
                        f"\n\nYour output MUST be a JSON object matching this schema:\n"
                        f"```json\n{json.dumps(output_schema, indent=2)}\n```\n"
                        "Output JSON only."
                    )
                    current_messages.append({"role": "assistant", "content": content})
                    current_messages.append({"role": "user", "content": f"Reformat your answer as JSON.{schema_hint}"})
                    try:
                        schema_response = await provider.chat(current_messages, output_schema=output_schema)
                        content = _resp_get(schema_response, "content", content)
                        total_tokens += _resp_get(schema_response, "tokens_used", 0)
                        steps.append({"step": step_num + 2, "type": "schema_coerce", "content": content})
                    except Exception:
                        pass
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
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc.get("id", f"tool_{step_num}"),
                    "name": tc.get("name", ""),
                    "input": tc.get("arguments", tc.get("input", {})),
                })
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
