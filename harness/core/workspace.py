"""Workspace — reads config/workspace.yaml for multi-repo discovery.

Usage:
    from harness.core.workspace import Workspace

    ws = Workspace()
    ws.domain_paths             # all domain directories
    ws.find_domain("backend_fastapi")  # find a domain by name
    ws.find_agent("backend_fastapi/FastAPICodeGenAgent/v1")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "workspace.yaml"


class Workspace:
    """Multi-repo workspace discovery from config/workspace.yaml."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        self._config_path = Path(config_path) if config_path else DEFAULT_CONFIG
        self._project_root = self._config_path.parent.parent
        self._data: dict[str, Any] = {}
        if self._config_path.exists():
            self._data = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}

    @property
    def repos(self) -> dict[str, dict[str, Any]]:
        raw = self._data.get("repos", {})
        resolved = {}
        for name, info in raw.items():
            info = dict(info)
            info["resolved_path"] = str((self._project_root / info.get("path", ".")).resolve())
            resolved[name] = info
        return resolved

    @property
    def domain_paths(self) -> list[Path]:
        domains = self._data.get("domains", [])
        return [
            (self._project_root / d.get("path", "")).resolve()
            for d in domains
            if (self._project_root / d.get("path", "")).exists()
        ]

    @property
    def tool_pack_dirs(self) -> list[Path]:
        return [
            (self._project_root / d).resolve()
            for d in self._data.get("tool_packs", [])
            if (self._project_root / d).exists()
        ]

    @property
    def mcp_config_path(self) -> Path | None:
        ref = self._data.get("mcp_config")
        if ref:
            p = self._project_root / ref
            return p if p.exists() else None
        return None

    def find_domain(self, name: str) -> Path | None:
        """Find a domain directory by name."""
        for dp in self.domain_paths:
            if dp.name == name:
                return dp
            domain_yaml = dp / "domain.yaml"
            if domain_yaml.exists():
                data = yaml.safe_load(domain_yaml.read_text(encoding="utf-8")) or {}
                if data.get("name") == name:
                    return dp
        return None

    def find_agent(self, agent_id: str) -> Path | None:
        """Find an agent directory by ID (e.g. 'backend_fastapi/FastAPICodeGenAgent/v1')."""
        parts = agent_id.split("/")
        if len(parts) < 2:
            return None
        domain_name = parts[0]
        agent_parts = "/".join(parts[1:])
        for dp in self.domain_paths:
            if dp.name == domain_name:
                candidate = dp / "agents" / agent_parts
                if candidate.exists():
                    return candidate
            candidate = dp / agent_parts
            if candidate.exists():
                return candidate
        return None

    def get_team_trusted_paths(self, team_name: str) -> list[str]:
        """Get resolved trusted paths for a team (if trusted: true).

        Returns all reference, target, and docs paths for a trusted team.
        Used by RuleEngine to allow file_read without permission prompts.
        """
        teams = self._data.get("teams", {})
        team = teams.get(team_name, {})
        if not team.get("trusted", False):
            return []
        paths: list[str] = []
        for key in ("reference", "target", "docs"):
            for item in team.get(key, []):
                if isinstance(item, dict) and "path" in item:
                    p = Path(item["path"])
                    if not p.is_absolute():
                        p = (self._project_root / p).resolve()
                    paths.append(str(p))
        return paths

    def summary(self) -> str:
        lines = ["Workspace:"]
        for name, info in self.repos.items():
            lines.append(f"  {name} ({info.get('role', '?')}): {info.get('resolved_path', '?')}")
        lines.append(f"\nDomains ({len(self.domain_paths)}):")
        for p in self.domain_paths:
            lines.append(f"  - {p}")
        lines.append(f"\nTool packs ({len(self.tool_pack_dirs)}):")
        for p in self.tool_pack_dirs:
            lines.append(f"  - {p}")
        mcp = self.mcp_config_path
        if mcp:
            from harness.tools.mcp_loader import load_mcp_config
            servers = load_mcp_config(mcp)
            lines.append(f"\nMCP servers ({len(servers)}):")
            for s in servers:
                tools = s.get("tools", [])
                lines.append(f"  - {s['name']} ({len(tools)} tools)")
        return "\n".join(lines)
