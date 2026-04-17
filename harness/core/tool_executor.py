"""ToolExecutor — parses LLM tool calls, routes to adapters, normalizes output."""

from __future__ import annotations

import time
from typing import Any


class ToolExecutionError(Exception):
    """Raised when a tool execution fails."""


class ToolExecutor:
    """Executes tool calls from LLM responses by routing to registered adapters.

    Responsibilities:
    - Check RuleEngine before every tool call (if configured)
    - Route to the correct tool adapter
    - Normalize output format
    - Track execution metrics
    """

    def __init__(self, rule_engine: Any = None) -> None:
        self._adapters: dict[str, Any] = {}
        self._execution_log: list[dict[str, Any]] = []
        self._rule_engine = rule_engine
        self._rule_context: Any = None  # Set by CompositionEngine per-step
        # G6 — FRAMEWORK_AUDIT_2026Q2: targeted-file allowlist for Builder
        # retries. When set (non-None), file_write/file_edit tool calls
        # MUST target a path inside this list; others are denied. Unset
        # (None) = no enforcement (backward compat).
        self._targeted_files: list[str] | None = None

    def register_adapter(self, tool_id: str, adapter: Any) -> None:
        """Register a tool adapter for a specific tool_id."""
        self._adapters[tool_id] = adapter

    def set_rule_context(self, context: Any) -> None:
        """Set the rule context for subsequent tool calls (agent permissions, etc.)."""
        self._rule_context = context

    def set_targeted_files(self, paths: list[str] | None) -> None:
        """Restrict file_write/file_edit tool calls to the given path allowlist.

        Called by AgentRunner before a retry step when the upstream QualityGate
        surfaced a list of files that need fixing. Passing None (or not calling
        this at all) disables enforcement — the default backward-compatible
        behaviour.

        Args:
            paths: list of file paths the retry step is allowed to write. Paths
                are compared exact-match against the tool call's `path` argument.
                Callers should normalise (e.g. strip leading `./`) before passing.
        """
        self._targeted_files = list(paths) if paths else None

    async def execute(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        """Execute a single tool call and return normalized output.

        Args:
            tool_call: Dict with 'id', 'name', 'arguments' keys from LLM response.

        Returns:
            Dict with 'output', 'success', 'duration_ms', 'tool_id' keys.
        """
        tool_name = tool_call.get("name", "")
        tool_id = tool_call.get("id", "")
        arguments = tool_call.get("arguments", {})

        # RuleEngine check (if configured)
        if self._rule_engine is not None:
            decision = self._rule_engine.check_tool_call(
                tool_name, arguments, self._rule_context,
            )
            if decision.denied:
                blocked_result = {
                    "tool_id": tool_id,
                    "tool_name": tool_name,
                    "output": f"Blocked by rule: {decision.reason}",
                    "success": False,
                    "duration_ms": 0,
                    "blocked_by_rule": True,
                    "rule_layer": decision.rule_layer,
                }
                self._execution_log.append(blocked_result)
                return blocked_result

        # G6 — targeted-files allowlist for Builder retries.
        # Enforced only when set_targeted_files() was called with a non-None
        # list. file_write / file_edit tool calls must target a path in the
        # allowlist; anything else is denied with a clear reason.
        if self._targeted_files is not None and tool_name in ("file_write", "file_edit"):
            requested_path = str(arguments.get("path", "")).strip()
            # Normalise leading "./" for comparison (agents sometimes emit it)
            normalised = requested_path[2:] if requested_path.startswith("./") else requested_path
            allowlist_normalised = {
                p[2:] if p.startswith("./") else p for p in self._targeted_files
            }
            if normalised not in allowlist_normalised:
                blocked_result = {
                    "tool_id": tool_id,
                    "tool_name": tool_name,
                    "output": (
                        f"Blocked by targeted-files allowlist: path {requested_path!r} "
                        f"is outside the retry's allowlist ({sorted(self._targeted_files)}). "
                        "Retry steps may only rewrite the specific files flagged by the "
                        "upstream gate."
                    ),
                    "success": False,
                    "duration_ms": 0,
                    "blocked_by_targeted_files": True,
                }
                self._execution_log.append(blocked_result)
                return blocked_result

        adapter = self._adapters.get(tool_name)
        if adapter is None:
            error_result = {
                "tool_id": tool_id,
                "tool_name": tool_name,
                "output": f"Error: No adapter registered for tool '{tool_name}'",
                "success": False,
                "duration_ms": 0,
            }
            self._execution_log.append(error_result)
            return error_result

        start = time.monotonic()
        try:
            if hasattr(adapter, "invoke"):
                raw_output = await adapter.invoke(tool_name, arguments)
            elif callable(adapter):
                raw_output = await adapter(tool_name, arguments)
            else:
                raise ToolExecutionError(f"Adapter for '{tool_name}' is not callable")

            duration_ms = int((time.monotonic() - start) * 1000)
            result = {
                "tool_id": tool_id,
                "tool_name": tool_name,
                "output": self._normalize_output(raw_output),
                "success": True,
                "duration_ms": duration_ms,
            }
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            result = {
                "tool_id": tool_id,
                "tool_name": tool_name,
                "output": f"Error: {exc}",
                "success": False,
                "duration_ms": duration_ms,
            }

        self._execution_log.append(result)
        return result

    def _normalize_output(self, raw_output: Any) -> str:
        """Normalize tool output to a string representation."""
        if isinstance(raw_output, str):
            return raw_output
        if isinstance(raw_output, dict):
            import json
            return json.dumps(raw_output, indent=2, default=str)
        return str(raw_output)

    @property
    def execution_log(self) -> list[dict[str, Any]]:
        """Return the execution log for metrics/auditing."""
        return list(self._execution_log)

    def clear_log(self) -> None:
        """Clear the execution log."""
        self._execution_log.clear()
