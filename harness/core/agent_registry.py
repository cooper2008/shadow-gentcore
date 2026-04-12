"""AgentRegistry — discovers and loads agent manifests from configured domain paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class AgentRegistry:
    """Discovers and loads all manifests from configured domain paths.

    Supports lookup by domain, pack, agent, tool, and workflow.
    """

    def __init__(self) -> None:
        self._agents: dict[str, dict[str, Any]] = {}
        self._domains: dict[str, list[str]] = {}
        self._scan_paths: list[Path] = []

    def add_scan_path(self, path: str | Path) -> None:
        """Add a directory path to scan for manifests."""
        self._scan_paths.append(Path(path))

    def scan(self) -> int:
        """Scan all registered paths for agent manifests.

        Looks for agent_manifest.yaml files and loads them.
        Returns count of manifests discovered.
        """
        count = 0
        for scan_path in self._scan_paths:
            if not scan_path.exists():
                continue
            for manifest_path in scan_path.rglob("agent_manifest.yaml"):
                try:
                    manifest = self._load_manifest(manifest_path)
                    agent_id = manifest.get("id", "")
                    if agent_id:
                        self._agents[agent_id] = manifest
                        domain = manifest.get("domain", "unknown")
                        self._domains.setdefault(domain, []).append(agent_id)
                        count += 1
                except Exception:
                    continue
        return count

    def register(self, agent_id: str, manifest: dict[str, Any]) -> None:
        """Manually register an agent manifest."""
        self._agents[agent_id] = manifest
        domain = manifest.get("domain", "unknown")
        self._domains.setdefault(domain, []).append(agent_id)

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Look up an agent manifest by ID."""
        return self._agents.get(agent_id)

    def list_agents(self, domain: str | None = None) -> list[str]:
        """List agent IDs, optionally filtered by domain."""
        if domain is not None:
            return list(self._domains.get(domain, []))
        return list(self._agents.keys())

    def list_domains(self) -> list[str]:
        """List all known domains."""
        return list(self._domains.keys())

    def find_by_category(self, category: str) -> list[str]:
        """Find agents matching a category."""
        return [
            aid for aid, m in self._agents.items()
            if m.get("category") == category
        ]

    def find_by_pack(self, pack: str) -> list[str]:
        """Find agents in a specific capability pack."""
        return [
            aid for aid, m in self._agents.items()
            if m.get("pack") == pack
        ]

    @staticmethod
    def _load_manifest(path: Path) -> dict[str, Any]:
        """Load a YAML manifest file."""
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    @property
    def agent_count(self) -> int:
        return len(self._agents)
