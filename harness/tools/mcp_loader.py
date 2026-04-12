"""MCP server loader — reads config/mcp_servers.yaml and registers MCP tool adapters.

Usage:
    from harness.tools.mcp_loader import register_mcp_tools
    register_mcp_tools(tool_executor)

    # In agent manifests, reference MCP tools via:
    #   tools:
    #     - name: query-docs
    #       pack: "toolpack://mcp/context7"
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import os

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path(
    os.environ.get("GENTCORE_MCP_CONFIG_PATH", "")
) if os.environ.get("GENTCORE_MCP_CONFIG_PATH") else Path(__file__).resolve().parent.parent.parent / "config" / "mcp_servers.yaml"

# Prefer agent_tools.MCPToolAdapter for new integrations.
# MCPToolBridge is retained for backward compatibility with existing config-driven setup.


class MCPToolBridge:
    """Bridges an MCP server into the ToolExecutor interface.

    Lazily spawns the MCP server subprocess on first invocation.
    Communicates via JSON-RPC 2.0 over stdin/stdout.
    """

    def __init__(self, server_name: str, command: str, transport: str = "stdio", env: dict[str, str] | None = None) -> None:
        self._server_name = server_name
        self._command = command
        self._transport = transport
        self._env = env or {}
        self._process: Any = None
        self._connected = False
        self._request_id = 0

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke a tool on the MCP server."""
        if not self._connected:
            await self._connect()

        if self._process is None or self._process.returncode is not None:
            return {"success": False, "error": f"MCP server '{self._server_name}' not running", "stdout": "", "stderr": ""}

        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }

        try:
            self._process.stdin.write((json.dumps(request) + "\n").encode())
            await self._process.stdin.drain()

            response_line = await asyncio.wait_for(self._process.stdout.readline(), timeout=30.0)
            response = json.loads(response_line.decode())
            result = response.get("result", {})

            content_parts = result.get("content", [])
            text_parts = [p.get("text", "") for p in content_parts if p.get("type") == "text"]
            output = "\n".join(text_parts) if text_parts else json.dumps(result)

            return {"success": not result.get("isError", False), "stdout": output, "stderr": "", "mcp_server": self._server_name}
        except asyncio.TimeoutError:
            return {"success": False, "error": "MCP call timed out", "stdout": "", "stderr": ""}
        except Exception as exc:
            return {"success": False, "error": str(exc), "stdout": "", "stderr": str(exc)}

    async def _connect(self) -> None:
        """Launch MCP server and send initialize handshake."""
        env = {**os.environ, **self._env}
        try:
            self._process = await asyncio.create_subprocess_shell(
                self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self._request_id += 1
            init_request = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "shadow-gentcore", "version": "0.1.0"},
                },
            }
            self._process.stdin.write((json.dumps(init_request) + "\n").encode())
            await self._process.stdin.drain()
            await asyncio.wait_for(self._process.stdout.readline(), timeout=10.0)
            self._connected = True
            logger.info("Connected to MCP server '%s'", self._server_name)
        except Exception as exc:
            logger.warning("Failed to connect to MCP server '%s': %s", self._server_name, exc)
            self._connected = False

    async def disconnect(self) -> None:
        """Shut down the MCP server process."""
        if self._process and self._process.returncode is None:
            self._process.terminate()
            await self._process.wait()
        self._connected = False


def load_mcp_config(config_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Load MCP server configuration from YAML."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("servers", [])


def register_mcp_tools(
    tool_executor: Any,
    config_path: str | Path | None = None,
) -> dict[str, MCPToolBridge]:
    """Read MCP config and register tool adapters in a ToolExecutor.

    Returns dict of server_name -> MCPToolBridge for lifecycle management.
    """
    servers = load_mcp_config(config_path)
    bridges: dict[str, MCPToolBridge] = {}

    for server in servers:
        name = server.get("name", "")
        command = server.get("command", "")
        if not name or not command:
            continue

        bridge = MCPToolBridge(
            server_name=name,
            command=command,
            transport=server.get("transport", "stdio"),
            env=server.get("env", {}),
        )
        bridges[name] = bridge

        for tool_def in server.get("tools", []):
            tool_name = tool_def.get("name", "") if isinstance(tool_def, dict) else str(tool_def)
            if tool_name:
                tool_executor.register_adapter(tool_name, bridge)

    return bridges
