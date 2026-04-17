"""RuleEngine — merges 6 rule layers, enforces on every tool call, hot-reloads config.

Rule layers (most restrictive wins):
    1. Platform rules (config/rules.yaml → platform:) — NON-NEGOTIABLE safety floor
    2. Category defaults (config/rules.yaml → category_overrides:)
    3. Domain rules (domain.yaml → workspace_policy)
    4. Agent rules (agent_manifest.yaml → permissions, constraints)
    5. Workflow rules (workflow step → permissions_override)
    6. Runtime rules (TaskEnvelope → overrides)

Usage:
    engine = RuleEngine()
    decision = engine.check_tool_call("shell_exec", {"command": "pytest"}, context)
    if decision.denied:
        return error_response(decision.reason)
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import os

import yaml

logger = logging.getLogger(__name__)

DEFAULT_RULES_PATH = Path(
    os.environ.get("GENTCORE_RULES_PATH", "")
) if os.environ.get("GENTCORE_RULES_PATH") else Path(__file__).resolve().parent.parent.parent / "config" / "rules.yaml"


def load_tool_actions_from_packs(pack_dir: str | Path) -> dict[str, str]:
    """Walk a toolpack directory and extract ``{tool_name: action_type}`` map.

    Reads every ``*.yaml`` under ``pack_dir`` (recursively). For each pack
    that declares a top-level ``action_type:`` (the B3 field), maps every
    tool in its ``tools:`` list to that action. Tools may be declared as
    either URI strings (``"tool://aws_s3"``) or inline dicts with ``id:``.

    Packs without an ``action_type`` are skipped (their tools fall through
    to the hardcoded fallback in ``RuleEngine._tool_to_action``). Malformed
    YAML is skipped silently.

    Intended bootstrap wiring::

        engine = RuleEngine()
        engine.register_tool_actions(
            load_tool_actions_from_packs(PATH_TO_AGENT_TOOLS_PACKS)
        )
    """
    root = Path(pack_dir)
    mapping: dict[str, str] = {}
    if not root.is_dir():
        return mapping

    for yaml_path in root.rglob("*.yaml"):
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        action = data.get("action_type")
        if not action:
            continue
        for tool in data.get("tools", []) or []:
            if isinstance(tool, dict):
                tool_id = tool.get("id", "")
            else:
                tool_id = str(tool)
            if not tool_id:
                continue
            tool_name = tool_id.split("tool://", 1)[-1]
            mapping[tool_name] = str(action)

    return mapping


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class RuleDecision:
    """Result of a rule check."""
    decision: Decision
    reason: str = ""
    rule_layer: str = ""  # which layer made the decision
    action: str = ""
    tool_name: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision == Decision.ALLOW

    @property
    def denied(self) -> bool:
        return self.decision == Decision.DENY


@dataclass
class RuleContext:
    """Context for rule evaluation — passed from ManifestLoader/CompositionEngine."""
    agent_category: str = ""
    agent_permissions: dict[str, str] = field(default_factory=dict)
    agent_constraints: dict[str, Any] = field(default_factory=dict)
    domain_policy: dict[str, Any] = field(default_factory=dict)
    workflow_overrides: dict[str, str] = field(default_factory=dict)
    runtime_overrides: dict[str, str] = field(default_factory=dict)
    workspace_root: str = ""
    trusted_paths: list[str] = field(default_factory=list)


class RuleEngine:
    """Merges rule layers and enforces on every tool call.

    Hot-reloads config/rules.yaml on each check if the file has changed.
    """

    # Debounce window for _hot_reload: at most one stat()/check per this many
    # milliseconds. File changes still take effect within one window. See H5
    # in FRAMEWORK_AUDIT_2026Q2.
    HOT_RELOAD_DEBOUNCE_MS: int = 500

    def __init__(self, rules_path: str | Path | None = None) -> None:
        self._rules_path = Path(rules_path) if rules_path else DEFAULT_RULES_PATH
        self._data: dict[str, Any] = {}
        self._last_mtime: float = 0.0
        self._last_reload_check: float = 0.0
        self._hot_reload_debounce_ms: int = self.HOT_RELOAD_DEBOUNCE_MS
        self._audit_log: list[dict[str, Any]] = []
        # H6 — pack-driven tool → action map. Populated at bootstrap from
        # toolpack metadata (ToolPackManifest.action_type). Takes precedence
        # over the hardcoded _HARDCODED_TOOL_ACTIONS fallback below.
        self._tool_action_map: dict[str, str] = {}
        self._load()

    def register_tool_actions(self, mapping: dict[str, str]) -> None:
        """Register a batch of ``{tool_name: action_type}`` entries.

        Call at bootstrap (typically from a boot helper that walks the agent-tools
        pack directory via :func:`load_tool_actions_from_packs`). Merges with
        any previously-registered entries; later calls overwrite earlier ones
        for the same tool.
        """
        self._tool_action_map.update(mapping)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: RuleContext | None = None,
    ) -> RuleDecision:
        """Check whether a tool call is permitted.

        This is the main entry point — called by ToolExecutor before every tool execution.
        """
        self._hot_reload()
        ctx = context or RuleContext()

        # Layer 0: Tool lockdown — only approved tools can be called
        from harness.tools.builtin import is_approved_tool
        if not is_approved_tool(tool_name):
            # Also check MCP tools (registered dynamically)
            from harness.tools.mcp_loader import load_mcp_config
            mcp_tools = set()
            for server in load_mcp_config():
                for t in server.get("tools", []):
                    tname = t.get("name", t) if isinstance(t, dict) else str(t)
                    mcp_tools.add(tname)
            if tool_name not in mcp_tools:
                decision = RuleDecision(
                    decision=Decision.DENY,
                    reason=f"Tool '{tool_name}' not in approved library. Only shared pack tools + MCP tools are allowed.",
                    rule_layer="lockdown",
                    action="tool_lockdown",
                    tool_name=tool_name,
                )
                self._audit(tool_name, arguments, decision)
                return decision

        # Layer 1: Platform rules (non-negotiable)
        platform_decision = self._check_platform(tool_name, arguments)
        if platform_decision.denied:
            self._audit(tool_name, arguments, platform_decision)
            return platform_decision

        # Trusted path fast-path: allow file_read in trusted workspaces
        # without further permission checks (inspired by claw-code trusted_roots)
        if ctx.trusted_paths:
            action = self._tool_to_action(tool_name)
            if action == "file_read":
                cmd_path = arguments.get("path", arguments.get("file", ""))
                if any(str(cmd_path).startswith(tp) for tp in ctx.trusted_paths):
                    decision = RuleDecision(
                        decision=Decision.ALLOW,
                        reason=f"Trusted path: {cmd_path}",
                        rule_layer="trusted",
                        action=action,
                        tool_name=tool_name,
                    )
                    self._audit(tool_name, arguments, decision)
                    return decision

        # Layer 2-6: Merge configurable permissions (most restrictive wins)
        action = self._tool_to_action(tool_name)
        effective = self._merge_permissions(action, ctx)

        decision = RuleDecision(
            decision=effective,
            reason=f"Merged permission for '{action}': {effective.value}",
            rule_layer="merged",
            action=action,
            tool_name=tool_name,
        )
        self._audit(tool_name, arguments, decision)
        return decision

    def check_content(self, content: str) -> RuleDecision:
        """Check content for sensitive data patterns."""
        self._hot_reload()
        platform = self._data.get("platform", {})
        for pattern in platform.get("sensitive_patterns", []):
            if re.search(pattern, content):
                return RuleDecision(
                    decision=Decision.DENY,
                    reason=f"Content matches sensitive pattern: {pattern}",
                    rule_layer="platform",
                )
        return RuleDecision(decision=Decision.ALLOW, rule_layer="platform")

    def check_path(self, path: str, context: RuleContext | None = None) -> RuleDecision:
        """Check whether a file path is within allowed bounds."""
        self._hot_reload()
        platform = self._data.get("platform", {})

        # Check global forbidden paths
        for forbidden in platform.get("global_forbidden_paths", []):
            if forbidden in str(path):
                return RuleDecision(
                    decision=Decision.DENY,
                    reason=f"Path matches forbidden pattern: {forbidden}",
                    rule_layer="platform",
                )

        # Check workspace bounds
        ctx = context or RuleContext()
        if platform.get("require_workspace_bounds") and ctx.workspace_root:
            resolved = Path(path).resolve()
            workspace = Path(ctx.workspace_root).resolve()
            try:
                resolved.relative_to(workspace)
            except ValueError:
                return RuleDecision(
                    decision=Decision.DENY,
                    reason=f"Path '{path}' escapes workspace '{ctx.workspace_root}'",
                    rule_layer="platform",
                )

        # Check domain allowed_paths
        domain_allowed = ctx.domain_policy.get("allowed_paths", [])
        if domain_allowed:
            if not any(str(path).startswith(a) for a in domain_allowed):
                return RuleDecision(
                    decision=Decision.DENY,
                    reason=f"Path not in domain allowed_paths: {domain_allowed}",
                    rule_layer="domain",
                )

        return RuleDecision(decision=Decision.ALLOW, rule_layer="merged")

    @property
    def audit_log(self) -> list[dict[str, Any]]:
        return list(self._audit_log)

    def clear_audit_log(self) -> None:
        self._audit_log.clear()

    # ------------------------------------------------------------------
    # Platform rules (Layer 1 — non-negotiable)
    # ------------------------------------------------------------------

    def _check_platform(self, tool_name: str, arguments: dict[str, Any]) -> RuleDecision:
        """Check platform safety rules. These CANNOT be overridden."""
        platform = self._data.get("platform", {})

        # Check blocked commands (for shell-like tools)
        if tool_name in ("shell_exec", "shell_command"):
            cmd = arguments.get("command", arguments.get("cmd", ""))
            for blocked in platform.get("blocked_commands", []):
                if blocked in cmd:
                    return RuleDecision(
                        decision=Decision.DENY,
                        reason=f"Blocked command: '{blocked}'",
                        rule_layer="platform",
                        action="shell_command",
                        tool_name=tool_name,
                    )

        # Check blocked content patterns in file writes
        if tool_name in ("file_write",):
            content = arguments.get("content", "")
            for pattern in platform.get("blocked_content_patterns", []):
                if re.search(pattern, content):
                    return RuleDecision(
                        decision=Decision.DENY,
                        reason=f"Content matches blocked pattern: {pattern}",
                        rule_layer="platform",
                        action="file_write",
                        tool_name=tool_name,
                    )

            # Check file size
            max_kb = platform.get("max_file_size_kb", 10000)
            content_kb = len(content.encode("utf-8")) / 1024
            if content_kb > max_kb:
                return RuleDecision(
                    decision=Decision.DENY,
                    reason=f"File content exceeds max size: {content_kb:.0f}KB > {max_kb}KB",
                    rule_layer="platform",
                    action="file_write",
                    tool_name=tool_name,
                )

        return RuleDecision(decision=Decision.ALLOW, rule_layer="platform")

    # ------------------------------------------------------------------
    # Permission merging (Layers 2-6)
    # ------------------------------------------------------------------

    def _merge_permissions(self, action: str, ctx: RuleContext) -> Decision:
        """Merge all configurable layers — most restrictive of explicitly-set layers wins.

        If no layer explicitly sets the action, fall back to platform defaults.
        Among layers that DO set the action, the most restrictive value wins.
        """
        # Collect explicit values from each layer
        explicit_values: list[str] = []

        # Layer 2: Category overrides
        cat_overrides = self._data.get("category_overrides", {}).get(ctx.agent_category, {})
        if action in cat_overrides:
            explicit_values.append(cat_overrides[action])

        # Layer 3: Domain policy
        domain_perms = ctx.domain_policy.get("permissions", {})
        if action in domain_perms:
            explicit_values.append(domain_perms[action])

        # Layer 4: Agent permissions
        if action in ctx.agent_permissions:
            explicit_values.append(ctx.agent_permissions[action])

        # Layer 5: Workflow overrides
        if action in ctx.workflow_overrides:
            explicit_values.append(ctx.workflow_overrides[action])

        # Layer 6: Runtime overrides
        if action in ctx.runtime_overrides:
            explicit_values.append(ctx.runtime_overrides[action])

        # If no layer explicitly set this action, use platform defaults
        if not explicit_values:
            defaults = self._data.get("defaults", {})
            return Decision(defaults.get(action, "ask"))

        # Most restrictive among all explicit layers
        result = explicit_values[0]
        for val in explicit_values[1:]:
            result = self._most_restrictive(result, val)
        return Decision(result)

    @staticmethod
    def _most_restrictive(a: str, b: str) -> str:
        """Return the most restrictive of two permission values."""
        order = {"deny": 0, "ask": 1, "allow": 2}
        return a if order.get(a, 1) <= order.get(b, 1) else b

    # Hardcoded fallback — used when no pack-driven entry is registered.
    # Preserved for backward compatibility with the pre-H6 behaviour.
    _HARDCODED_TOOL_ACTIONS: dict[str, str] = {
        "file_read": "file_read",
        "file_write": "file_write",
        "file_list": "file_read",
        "file_delete": "file_delete",
        "file_exists": "file_read",
        "list_dir": "file_read",
        "shell_exec": "shell_command",
        "search_code": "file_read",
        "search_files": "file_read",
    }

    def _tool_to_action(self, tool_name: str) -> str:
        """Map a tool name to a permission action.

        Resolution order (H6):
          1. Pack-driven map populated via :meth:`register_tool_actions`
             (sourced from ToolPackManifest.action_type — the B3 field)
          2. Hardcoded fallback for core filesystem/shell tools
          3. ``shell_command`` (most restrictive) for truly unknown tools
        """
        if tool_name in self._tool_action_map:
            return self._tool_action_map[tool_name]
        return self._HARDCODED_TOOL_ACTIONS.get(tool_name, "shell_command")

    # ------------------------------------------------------------------
    # Hot-reload + audit
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load rules from YAML file."""
        if self._rules_path.exists():
            self._data = yaml.safe_load(self._rules_path.read_text(encoding="utf-8")) or {}
            self._last_mtime = self._rules_path.stat().st_mtime
        else:
            self._data = {}
            self._last_mtime = 0.0

    def _hot_reload(self) -> None:
        """Reload config if the file changed since last load.

        Debounced: skips the filesystem stat entirely when called more
        frequently than ``HOT_RELOAD_DEBOUNCE_MS``. File edits still take
        effect within one debounce window (default 500 ms).
        """
        now = time.time()
        window_s = self._hot_reload_debounce_ms / 1000.0
        if now - self._last_reload_check < window_s:
            return
        self._last_reload_check = now

        if not self._rules_path.exists():
            return
        try:
            current_mtime = self._rules_path.stat().st_mtime
            if current_mtime > self._last_mtime:
                logger.info("Hot-reloading rules from %s", self._rules_path)
                self._load()
        except OSError:
            pass

    def _audit(self, tool_name: str, arguments: dict[str, Any], decision: RuleDecision) -> None:
        """Record an audit log entry."""
        platform = self._data.get("platform", {})
        if not platform.get("audit_all_tool_calls", True):
            return
        self._audit_log.append({
            "timestamp": time.time(),
            "tool_name": tool_name,
            "action": decision.action or self._tool_to_action(tool_name),
            "decision": decision.decision.value,
            "reason": decision.reason,
            "rule_layer": decision.rule_layer,
            "arguments_keys": list(arguments.keys()),
        })
