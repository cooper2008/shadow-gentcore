"""PermissionResolver — resolves permissions from agent, domain, and runtime layers."""

from __future__ import annotations

from enum import Enum
from typing import Any


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionResolver:
    """Resolves effective permissions by merging agent, domain, and runtime layers.

    Resolution order (most restrictive wins):
    1. Runtime overrides (e.g., cloud = fail-closed)
    2. Domain defaults
    3. Agent-specific permissions

    Supports audit trail of permission decisions.
    """

    def __init__(
        self,
        runtime_mode: str = "local",
        domain_defaults: dict[str, str] | None = None,
    ) -> None:
        self._runtime_mode = runtime_mode
        self._domain_defaults = domain_defaults or {}
        self._audit_log: list[dict[str, Any]] = []

    def resolve(
        self,
        action: str,
        agent_permissions: Any | None = None,
        context: dict[str, Any] | None = None,
    ) -> PermissionDecision:
        """Resolve whether an action is permitted.

        Args:
            action: The action to check (e.g., 'file_edit', 'shell_command').
            agent_permissions: Agent-level permission config (Pydantic model or dict).
            context: Additional context for the decision.

        Returns:
            PermissionDecision: ALLOW, DENY, or ASK.
        """
        # Cloud mode: fail-closed by default
        if self._runtime_mode == "cloud":
            decision = self._resolve_cloud(action, agent_permissions)
        else:
            decision = self._resolve_local(action, agent_permissions)

        self._audit_log.append({
            "action": action,
            "decision": decision.value,
            "runtime_mode": self._runtime_mode,
            "context": context,
        })
        return decision

    def _resolve_local(self, action: str, agent_permissions: Any) -> PermissionDecision:
        """Local mode: interactive, defaults to ASK for unknown actions."""
        perm_value = self._get_perm_value(action, agent_permissions)
        if perm_value == "allow":
            return PermissionDecision.ALLOW
        if perm_value == "deny":
            return PermissionDecision.DENY
        return PermissionDecision.ASK

    def _resolve_cloud(self, action: str, agent_permissions: Any) -> PermissionDecision:
        """Cloud mode: fail-closed, only explicit allow passes."""
        perm_value = self._get_perm_value(action, agent_permissions)
        if perm_value == "allow":
            return PermissionDecision.ALLOW
        return PermissionDecision.DENY

    def _get_perm_value(self, action: str, agent_permissions: Any) -> str | None:
        """Extract permission value from agent permissions or domain defaults."""
        # Try agent permissions first
        if agent_permissions is not None:
            if hasattr(agent_permissions, "model_dump"):
                perm_dict = agent_permissions.model_dump()
            elif isinstance(agent_permissions, dict):
                perm_dict = agent_permissions
            else:
                perm_dict = {}
            if action in perm_dict:
                return str(perm_dict[action])

        # Fall back to domain defaults
        return self._domain_defaults.get(action)

    @property
    def audit_log(self) -> list[dict[str, Any]]:
        return list(self._audit_log)

    def clear_audit_log(self) -> None:
        self._audit_log.clear()
