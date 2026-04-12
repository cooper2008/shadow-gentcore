"""Tests for PermissionResolver."""

from __future__ import annotations

import pytest

from harness.core.permissions import PermissionResolver, PermissionDecision


class TestPermissionResolver:
    def test_local_allow(self) -> None:
        resolver = PermissionResolver(runtime_mode="local")
        decision = resolver.resolve(
            "file_edit",
            agent_permissions={"file_edit": "allow"},
        )
        assert decision == PermissionDecision.ALLOW

    def test_local_deny(self) -> None:
        resolver = PermissionResolver(runtime_mode="local")
        decision = resolver.resolve(
            "shell_command",
            agent_permissions={"shell_command": "deny"},
        )
        assert decision == PermissionDecision.DENY

    def test_local_ask_for_unknown(self) -> None:
        resolver = PermissionResolver(runtime_mode="local")
        decision = resolver.resolve("unknown_action", agent_permissions={})
        assert decision == PermissionDecision.ASK

    def test_cloud_explicit_allow(self) -> None:
        resolver = PermissionResolver(runtime_mode="cloud")
        decision = resolver.resolve(
            "file_edit",
            agent_permissions={"file_edit": "allow"},
        )
        assert decision == PermissionDecision.ALLOW

    def test_cloud_denies_unknown(self) -> None:
        resolver = PermissionResolver(runtime_mode="cloud")
        decision = resolver.resolve("shell_command", agent_permissions={})
        assert decision == PermissionDecision.DENY

    def test_cloud_denies_ask(self) -> None:
        resolver = PermissionResolver(runtime_mode="cloud")
        decision = resolver.resolve(
            "shell_command",
            agent_permissions={"shell_command": "ask"},
        )
        assert decision == PermissionDecision.DENY

    def test_domain_defaults_fallback(self) -> None:
        resolver = PermissionResolver(
            runtime_mode="local",
            domain_defaults={"file_edit": "allow"},
        )
        decision = resolver.resolve("file_edit", agent_permissions={})
        assert decision == PermissionDecision.ALLOW

    def test_agent_overrides_domain(self) -> None:
        resolver = PermissionResolver(
            runtime_mode="local",
            domain_defaults={"file_edit": "allow"},
        )
        decision = resolver.resolve(
            "file_edit",
            agent_permissions={"file_edit": "deny"},
        )
        assert decision == PermissionDecision.DENY

    def test_audit_log(self) -> None:
        resolver = PermissionResolver(runtime_mode="local")
        resolver.resolve("file_edit", agent_permissions={"file_edit": "allow"})
        resolver.resolve("shell_command", agent_permissions={})
        assert len(resolver.audit_log) == 2
        assert resolver.audit_log[0]["action"] == "file_edit"
        assert resolver.audit_log[0]["decision"] == "allow"

    def test_clear_audit_log(self) -> None:
        resolver = PermissionResolver(runtime_mode="local")
        resolver.resolve("file_edit", agent_permissions={"file_edit": "allow"})
        resolver.clear_audit_log()
        assert len(resolver.audit_log) == 0
