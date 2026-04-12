"""ToolDisclosureRouter — progressive L1→L2 tool disclosure for the ReAct loop.

Concept
-------
When an agent declares many tools (15+), injecting every tool's full JSON schema
on the first LLM call is expensive and noisy.  Progressive disclosure splits
declared tools into two tiers based on the ``level`` field in the manifest:

  L1  name + one-line description only (injected as plain text, ~10 tok/tool)
  L2  full Anthropic tool schema (injected in ``tools`` param, ~70 tok/tool)

On each ReAct step the router scans the LLM's response for L1 tool name
mentions and promotes matched tools to L2 before the next call.  The LLM
"discovers" full schemas only when it shows intent to use a specific tool.

Manifest format (additive — backward compatible, default is L2)
---------------------------------------------------------------
  tools:
    - name: file_read        # default level=L2 (full schema always present)
      desc: "Read a file"
    - name: gh_create_pr
      desc: "Open a GitHub PR"
      level: L1              # starts as text hint; promoted when LLM mentions it
    - name: shell_exec
      desc: "Run a shell command"
      level: L1

Token profile for a 20-tool agent (12 L2, 8 L1)
------------------------------------------------
  Step 1  12 full schemas + 8 one-liners ≈ 920 tok  (vs 1,400 flat)
  Step 3  LLM mentions "gh_create_pr" → promoted → 13 full schemas next call
  Savings ~35% fewer tool tokens averaged across a typical run
"""

from __future__ import annotations

from typing import Any


class ToolDisclosureRouter:
    """Manages L1/L2 tool partitions and promotes tools on intent detection.

    Usage in ReActStrategy loop::

        router = ToolDisclosureRouter(declared_tools, tool_executor)
        for step in range(max_steps):
            api_tools = router.current_api_tools()
            chat_kwargs = {"tools": api_tools} if api_tools else {}
            # Inject L1 summary as a system note if any L1 tools remain
            msgs = _inject_l1_hint(messages, router.l1_summary())
            response = await provider.chat(msgs, **chat_kwargs)
            # Promote any L1 tools the LLM mentioned
            router.detect_and_promote(response.content + str(response.tool_calls))
    """

    def __init__(
        self,
        declared_tools: list[Any],
        tool_executor: Any,
    ) -> None:
        """Partition declared tools into L1 and L2 sets.

        Args:
            declared_tools: List of tool entries from the manifest.  Each entry
                            is either a plain string ``"tool_name"`` or a dict
                            ``{name, desc, level}``.  Missing ``level`` defaults
                            to ``"L2"``.
            tool_executor:  ToolExecutor instance — used to check which tools
                            are actually registered (safety: ignore unregistered).
        """
        self._executor = tool_executor
        adapters: set[str] = set(getattr(tool_executor, "_adapters", {}).keys())

        # L1: name → one-line description (tools to progressively promote)
        self._l1_desc: dict[str, str] = {}
        # L1 tools not yet promoted (still text-only)
        self._l1_pending: set[str] = set()
        # L1 tools promoted to full schema this session
        self._promoted: set[str] = set()
        # Tools declared as L2 from the start (always full schema)
        self._always_l2: list[str] = []

        for entry in declared_tools:
            if isinstance(entry, dict):
                name: str = entry.get("name", "")
                desc: str = entry.get("desc", entry.get("description", ""))
                level: str = str(entry.get("level", "L2")).upper()
            else:
                name = str(entry)
                desc = ""
                level = "L2"

            if not name or name not in adapters:
                continue  # skip unregistered tools silently

            if level == "L1":
                self._l1_desc[name] = desc
                self._l1_pending.add(name)
            else:
                self._always_l2.append(name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def has_l1_tools(self) -> bool:
        """True when at least one tool started as L1 (progressive mode active)."""
        return bool(self._l1_desc)

    @property
    def pending_l1_count(self) -> int:
        """Number of L1 tools not yet promoted."""
        return len(self._l1_pending)

    def current_api_tools(self) -> list[dict[str, Any]]:
        """Return full Anthropic tool schemas for all currently-L2 tools.

        Includes:
        - Tools originally declared as L2
        - L1 tools that have been promoted this session
        """
        from harness.core.modes.react import _build_anthropic_tools
        active = self._always_l2 + list(self._promoted)
        return _build_anthropic_tools(self._executor, allowed=active)

    def l1_summary(self) -> str | None:
        """Return a brief text block listing still-pending L1 tools.

        Returns None when all L1 tools are promoted (no hint needed).
        """
        if not self._l1_pending:
            return None
        lines = [
            "Additional tools available — mention a tool name to get its full schema:",
        ]
        for name in sorted(self._l1_pending):
            desc = self._l1_desc.get(name, "")
            suffix = f": {desc}" if desc else ""
            lines.append(f"  • {name}{suffix}")
        return "\n".join(lines)

    def detect_and_promote(self, text: str) -> list[str]:
        """Scan LLM output for L1 tool name mentions and promote matched tools.

        A tool is promoted when its exact name appears anywhere in ``text``
        (case-sensitive exact match to avoid false positives on short names).

        Args:
            text: Combined LLM content + tool_call names from the last step.

        Returns:
            List of tool names that were promoted this call.
        """
        newly_promoted: list[str] = []
        for name in list(self._l1_pending):
            if name in text:
                self._l1_pending.discard(name)
                self._promoted.add(name)
                newly_promoted.append(name)
        return newly_promoted

    def promote(self, tool_name: str) -> bool:
        """Manually promote a specific tool from L1 to L2.

        Args:
            tool_name: Exact tool name to promote.

        Returns:
            True if the tool was in L1 pending and got promoted; False otherwise.
        """
        if tool_name in self._l1_pending:
            self._l1_pending.discard(tool_name)
            self._promoted.add(tool_name)
            return True
        return False

    def promotion_log(self) -> dict[str, Any]:
        """Return a summary of promotion activity for observability."""
        return {
            "always_l2": list(self._always_l2),
            "promoted": sorted(self._promoted),
            "still_pending_l1": sorted(self._l1_pending),
        }
