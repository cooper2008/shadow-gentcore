"""Guardrails — schema validation, command allowlist/blocklist, path bounds, content risk."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class GuardrailViolation(Exception):
    """Raised when a guardrail check fails."""


class Guardrails:
    """Enforces safety guardrails on agent actions.

    Checks:
    - Command allowlist/blocklist
    - Path bounds (prevent escaping workspace)
    - Content risk (stub for future ML-based checks)
    """

    def __init__(
        self,
        workspace_root: str | Path | None = None,
        allowed_commands: list[str] | None = None,
        blocked_commands: list[str] | None = None,
        allowed_paths: list[str] | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root) if workspace_root else None
        self._allowed_commands = set(allowed_commands) if allowed_commands else None
        self._blocked_commands = set(blocked_commands or [
            "rm -rf /", "mkfs", "dd if=", ":(){ :|:& };:",
            "chmod -R 777 /", "shutdown", "reboot",
        ])
        self._allowed_paths = [Path(p) for p in (allowed_paths or [])]

    def check_command(self, command: str) -> None:
        """Validate a shell command against allowlist/blocklist.

        Raises GuardrailViolation if command is blocked.
        """
        cmd_stripped = command.strip()

        for blocked in self._blocked_commands:
            if blocked in cmd_stripped:
                raise GuardrailViolation(f"Blocked command pattern: '{blocked}'")

        if self._allowed_commands is not None:
            base_cmd = cmd_stripped.split()[0] if cmd_stripped else ""
            if base_cmd not in self._allowed_commands:
                raise GuardrailViolation(
                    f"Command '{base_cmd}' not in allowlist: {self._allowed_commands}"
                )

    def check_path(self, path: str | Path) -> None:
        """Validate a file path is within workspace bounds.

        Raises GuardrailViolation if path escapes workspace.
        """
        resolved = Path(path).resolve()

        if self._workspace_root is not None:
            workspace_resolved = self._workspace_root.resolve()
            try:
                resolved.relative_to(workspace_resolved)
            except ValueError:
                raise GuardrailViolation(
                    f"Path '{resolved}' escapes workspace '{workspace_resolved}'"
                )

        if self._allowed_paths:
            if not any(
                self._is_under(resolved, allowed.resolve())
                for allowed in self._allowed_paths
            ):
                raise GuardrailViolation(
                    f"Path '{resolved}' not under any allowed path"
                )

    def check_content(self, content: str) -> None:
        """Stub content risk check. Currently checks for obvious sensitive patterns.

        Raises GuardrailViolation if risky content detected.
        """
        sensitive_patterns = [
            r"(?i)(api[_-]?key|secret[_-]?key|password)\s*[:=]\s*['\"][^'\"]+['\"]",
            r"(?i)BEGIN\s+(RSA\s+)?PRIVATE\s+KEY",
        ]
        for pattern in sensitive_patterns:
            if re.search(pattern, content):
                raise GuardrailViolation(
                    f"Content contains potentially sensitive data matching pattern: {pattern}"
                )

    @staticmethod
    def _is_under(child: Path, parent: Path) -> bool:
        """Check if child path is under parent path."""
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False
