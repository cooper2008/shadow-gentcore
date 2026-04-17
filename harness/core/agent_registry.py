"""AgentRegistry — discovers, caches, and resolves agent manifests + paths.

Audit H-REG: pre-refactor, ManifestLoader.build_step_configs used a
3-path fallback (domain/<AgentName>/<v>, domain/<sub>/<AgentName>/<v>,
project_root/<sub>/<AgentName>/<v>) on every step of every workflow.
This file caches the result at boot, providing `{agent_id → Path}` lookups
in O(1) and a single `resolve_path` entry point that preserves the
legacy fallback as a safety net.

Responsibilities:
  - Scan configured roots for `agent_manifest.yaml` files at boot
  - Cache {agent_id: manifest_dict} and {agent_id: directory_path}
  - Expose lookups by id, domain, category, and pack
  - Expose `resolve_path(agent_id, domain_root, project_root)` that
    consults the cache first, then falls back to the 3-path scan for
    agents not discovered at boot time (e.g., dynamically added).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Discovers and loads all manifests from configured scan paths.

    Supports lookup by agent_id, domain, category, and pack. Caches the
    *directory path* of every discovered agent so downstream code doesn't
    need to reconstruct paths from agent_id strings.
    """

    def __init__(self) -> None:
        self._agents: dict[str, dict[str, Any]] = {}
        self._domains: dict[str, list[str]] = {}
        self._paths: dict[str, Path] = {}  # H-REG: agent_id → directory path
        self._scan_paths: list[Path] = []

    # ------------------------------------------------------------------
    # Scan + registration
    # ------------------------------------------------------------------

    def add_scan_path(self, path: str | Path) -> None:
        """Add a directory path to scan for manifests."""
        self._scan_paths.append(Path(path))

    def scan(self) -> int:
        """Scan all registered paths for agent manifests.

        Looks for agent_manifest.yaml files and loads them. Records each
        agent's directory path so resolve_path() is O(1).

        Returns:
            Count of manifests discovered.
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
                        self._paths[agent_id] = manifest_path.parent
                        domain = manifest.get("domain", "unknown")
                        self._domains.setdefault(domain, []).append(agent_id)
                        count += 1
                except Exception as exc:
                    logger.debug("Skipping malformed manifest at %s: %s", manifest_path, exc)
                    continue
        return count

    def register(self, agent_id: str, manifest: dict[str, Any], path: str | Path | None = None) -> None:
        """Manually register an agent manifest (for tests + dynamic agents).

        Args:
            agent_id: canonical agent identifier.
            manifest: loaded manifest dict.
            path: optional directory path. When omitted, resolve_path falls
                back to filesystem scans for this agent.
        """
        self._agents[agent_id] = manifest
        domain = manifest.get("domain", "unknown")
        self._domains.setdefault(domain, []).append(agent_id)
        if path is not None:
            self._paths[agent_id] = Path(path)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Look up an agent manifest by ID. None if not registered."""
        return self._agents.get(agent_id)

    def get_path(self, agent_id: str) -> Path | None:
        """Return the directory path for a registered agent, or None."""
        return self._paths.get(agent_id)

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

    # ------------------------------------------------------------------
    # Path resolution — H-REG core
    # ------------------------------------------------------------------

    def resolve_path(
        self,
        agent_id: str,
        domain_root: str | Path,
        project_root: str | Path | None = None,
    ) -> Path | None:
        """Resolve an agent's directory path from its id.

        Order of attempts:
          1. Cache hit (`self._paths[agent_id]`) — O(1).
          2. Legacy 3-path scan, preserved for agents not yet in the cache:
             - `{domain_root}/agents/<AgentName>/<version>/`
             - `{domain_root}/agents/<sub>/<AgentName>/<version>/`
             - `{project_root}/agents/<sub>/<AgentName>/<version>/`
          3. Fallback: `{domain_root}/agents/<agent_id_as_path>`
          4. Returns None when no candidate exists on disk.

        Cache misses that resolve via the legacy scan are written back to
        the cache so the next lookup is O(1).
        """
        cached = self._paths.get(agent_id)
        if cached is not None and cached.exists():
            return cached

        parts = agent_id.split("/")
        domain_root = Path(domain_root)
        project_root = Path(project_root) if project_root else Path.cwd()

        candidates: list[Path] = []
        if len(parts) >= 3:
            sub, agent_name, agent_version = parts[0], parts[1], parts[2]
            candidates = [
                domain_root / "agents" / agent_name / agent_version,
                domain_root / "agents" / sub / agent_name / agent_version,
                project_root / "agents" / sub / agent_name / agent_version,
            ]
        elif len(parts) == 2:
            candidates = [domain_root / "agents" / parts[0] / parts[1]]

        candidates.append(domain_root / "agents" / agent_id)  # final fallback

        for candidate in candidates:
            if candidate.exists():
                self._paths[agent_id] = candidate  # cache the hit
                return candidate
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _load_manifest(path: Path) -> dict[str, Any]:
        """Load a YAML manifest file."""
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    @property
    def agent_count(self) -> int:
        return len(self._agents)

    def clear(self) -> None:
        """Reset the registry — mostly for tests."""
        self._agents.clear()
        self._domains.clear()
        self._paths.clear()
        self._scan_paths.clear()
