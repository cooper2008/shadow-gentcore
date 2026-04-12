"""Tests for MCP server config loader and workspace."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.tools.mcp_loader import load_mcp_config, register_mcp_tools, MCPToolBridge
from harness.core.tool_executor import ToolExecutor


class TestLoadMcpConfig:
    def test_loads_servers(self, tmp_path: Path) -> None:
        config = {"servers": [{"name": "s1", "command": "echo s1", "tools": [{"name": "t1"}]}]}
        p = tmp_path / "mcp.yaml"
        p.write_text(yaml.dump(config))
        servers = load_mcp_config(p)
        assert len(servers) == 1
        assert servers[0]["name"] == "s1"

    def test_returns_empty_for_missing_file(self) -> None:
        assert load_mcp_config("/nonexistent") == []

    def test_returns_empty_for_empty_file(self, tmp_path: Path) -> None:
        (tmp_path / "e.yaml").write_text("")
        assert load_mcp_config(tmp_path / "e.yaml") == []

    def test_loads_default_config(self) -> None:
        servers = load_mcp_config()
        # Default config should have at least context7
        assert any(s.get("name") == "context7" for s in servers)


class TestRegisterMcpTools:
    def test_registers_tools(self, tmp_path: Path) -> None:
        config = {"servers": [{"name": "ctx", "command": "echo", "tools": [{"name": "qa"}, {"name": "qb"}]}]}
        p = tmp_path / "mcp.yaml"
        p.write_text(yaml.dump(config))
        executor = ToolExecutor()
        bridges = register_mcp_tools(executor, config_path=p)
        assert "ctx" in bridges
        assert "qa" in executor._adapters
        assert "qb" in executor._adapters

    def test_skips_nameless_server(self, tmp_path: Path) -> None:
        config = {"servers": [{"command": "echo", "tools": [{"name": "t"}]}]}
        p = tmp_path / "mcp.yaml"
        p.write_text(yaml.dump(config))
        executor = ToolExecutor()
        assert register_mcp_tools(executor, config_path=p) == {}


class TestMCPToolBridge:
    def test_creates(self) -> None:
        b = MCPToolBridge("test", "echo test")
        assert b._connected is False

    @pytest.mark.asyncio
    async def test_invoke_without_connection(self) -> None:
        b = MCPToolBridge("fake", "nonexistent_cmd_xyz")
        result = await b.invoke("tool", {})
        assert result["success"] is False


class TestWorkspace:
    def test_loads(self) -> None:
        from harness.core.workspace import Workspace
        ws = Workspace()
        assert isinstance(ws.repos, dict)
        assert isinstance(ws.domain_paths, list)

    def test_summary(self) -> None:
        from harness.core.workspace import Workspace
        summary = Workspace().summary()
        assert "Workspace:" in summary

    def test_finds_backend_fastapi(self) -> None:
        from harness.core.workspace import Workspace
        ws = Workspace()
        result = ws.find_domain("backend_fastapi")
        if result:
            assert "backend_fastapi" in str(result)

    def test_mcp_config_path(self) -> None:
        from harness.core.workspace import Workspace
        ws = Workspace()
        mcp = ws.mcp_config_path
        if mcp:
            assert mcp.name == "mcp_servers.yaml"


class TestMcpCli:
    def test_mcp_list(self) -> None:
        from click.testing import CliRunner
        from harness.cli.ai import cli
        result = CliRunner().invoke(cli, ["mcp", "list"])
        assert result.exit_code == 0
        assert "context7" in result.output

    def test_workspace_command(self) -> None:
        from click.testing import CliRunner
        from harness.cli.ai import cli
        result = CliRunner().invoke(cli, ["workspace"])
        assert result.exit_code == 0
        assert "Workspace:" in result.output
