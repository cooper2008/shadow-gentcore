"""Tests for B6 — MCP server registry templates (fix/B6-mcp-templates).

Every commonly-needed ops-oriented MCP server should ship as a commented
template in config/mcp_servers.yaml so `./ai mcp add <name>` is a one-liner.

These templates are COMMENTED OUT by default — the test verifies their
presence in the raw file content, not in the parsed server list.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.tools.mcp_loader import load_mcp_config


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MCP_CONFIG = REPO_ROOT / "config" / "mcp_servers.yaml"


class TestActiveServersStillLoad:
    """Backward compatibility: don't break existing enabled servers."""

    def test_context7_still_present(self) -> None:
        servers = load_mcp_config(MCP_CONFIG)
        assert any(s.get("name") == "context7" for s in servers), (
            "context7 server must remain enabled after B6 (backward compat)"
        )


class TestExpectedTemplatesShipped:
    """Ops-oriented MCP servers should appear as commented templates."""

    EXPECTED_TEMPLATES = [
        "aws",
        "k8s",
        "datadog",
        "sentry",
        "opsgenie",
        "grafana",
    ]

    @pytest.mark.parametrize("server_name", EXPECTED_TEMPLATES)
    def test_template_block_present(self, server_name: str) -> None:
        """Each expected MCP server ships as a commented `- name: <server>` line."""
        content = MCP_CONFIG.read_text(encoding="utf-8")
        marker = f"# - name: {server_name}"
        assert marker in content, (
            f"Expected commented template `{marker}` in {MCP_CONFIG} — "
            f"B6 requires ops-oriented MCP servers to ship as opt-in templates"
        )

    @pytest.mark.parametrize("server_name", EXPECTED_TEMPLATES)
    def test_template_has_command_line(self, server_name: str) -> None:
        """Each template must include a `# command:` line so users know how to enable it."""
        content = MCP_CONFIG.read_text(encoding="utf-8")
        lines = content.splitlines()
        template_start = None
        for i, line in enumerate(lines):
            if f"# - name: {server_name}" in line:
                template_start = i
                break
        assert template_start is not None
        # Within 10 lines of the name, a command: field must exist
        block = "\n".join(lines[template_start:template_start + 12])
        assert "command:" in block, (
            f"{server_name} template missing command: line within its block"
        )


class TestTemplatesDeclareCapabilities:
    """Templates should tag `provides:` so they integrate with the capability map (B2)."""

    @pytest.mark.parametrize("server_name,expected_capability", [
        ("aws", "cloud_query"),
        ("k8s", "cloud_query"),
        ("datadog", "observability"),
        ("sentry", "observability"),
        ("opsgenie", "alerting"),
        ("grafana", "observability"),
    ])
    def test_template_provides_relevant_capability(self, server_name: str, expected_capability: str) -> None:
        content = MCP_CONFIG.read_text(encoding="utf-8")
        lines = content.splitlines()
        template_start = None
        for i, line in enumerate(lines):
            if f"# - name: {server_name}" in line:
                template_start = i
                break
        assert template_start is not None
        block = "\n".join(lines[template_start:template_start + 15])
        assert expected_capability in block, (
            f"{server_name} template should list `{expected_capability}` in its provides: "
            f"so the capability map (B2) can route to it when enabled"
        )
