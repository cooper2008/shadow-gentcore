"""Tests for Guardrails."""

from __future__ import annotations

import os
import tempfile

import pytest

from harness.core.guardrails import Guardrails, GuardrailViolation


class TestGuardrails:
    def test_allowed_command(self) -> None:
        g = Guardrails(allowed_commands=["pytest", "ruff", "echo"])
        g.check_command("pytest --tb=short")  # should not raise

    def test_blocked_command(self) -> None:
        g = Guardrails()
        with pytest.raises(GuardrailViolation, match="Blocked command"):
            g.check_command("rm -rf /")

    def test_command_not_in_allowlist(self) -> None:
        g = Guardrails(allowed_commands=["pytest"])
        with pytest.raises(GuardrailViolation, match="not in allowlist"):
            g.check_command("curl http://evil.com")

    def test_no_allowlist_allows_unblocked(self) -> None:
        g = Guardrails()
        g.check_command("echo hello")  # should not raise

    def test_path_within_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            g = Guardrails(workspace_root=tmpdir)
            valid_path = os.path.join(tmpdir, "src", "main.py")
            g.check_path(valid_path)  # should not raise

    def test_path_escapes_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            g = Guardrails(workspace_root=tmpdir)
            with pytest.raises(GuardrailViolation, match="escapes workspace"):
                g.check_path("/etc/passwd")

    def test_path_traversal_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            g = Guardrails(workspace_root=tmpdir)
            with pytest.raises(GuardrailViolation, match="escapes workspace"):
                g.check_path(os.path.join(tmpdir, "..", "..", "etc", "passwd"))

    def test_content_clean(self) -> None:
        g = Guardrails()
        g.check_content("Normal code content here")  # should not raise

    def test_content_detects_api_key(self) -> None:
        g = Guardrails()
        with pytest.raises(GuardrailViolation, match="sensitive"):
            g.check_content('api_key = "sk-1234567890abcdef"')

    def test_content_detects_private_key(self) -> None:
        g = Guardrails()
        with pytest.raises(GuardrailViolation, match="sensitive"):
            g.check_content("-----BEGIN RSA PRIVATE KEY-----\nMIIEpA...")

    def test_no_workspace_root_skips_path_check(self) -> None:
        g = Guardrails()
        g.check_path("/any/path/is/fine")  # no workspace = no bounds check
