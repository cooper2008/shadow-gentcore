"""PackIndex — boot-time cache of toolpack metadata (G-TDI).

Pre-G-TDI, ToolDiscoveryAgent's system prompt directed it to `search_files`
across agent-tools/packs/ every genesis run. With 43+ packs × ~5 tools
each, that meant ~200 manifest reads per run even when the answer hadn't
changed since last boot.

PackIndex solves this by scanning all pack YAMLs once at boot and
exposing O(1) lookups:
  - `get_pack(id) -> metadata dict`
  - `find_by_capability(name) -> list[pack_id]`  (uses B3's `provides` field)
  - `find_by_action_type(name) -> list[pack_id]`  (uses B3's `action_type`)
  - `find_tool(tool_uri) -> pack_id | None`
  - `all_packs() -> list[pack_id]`
  - `metadata_snapshot() -> full index for serialisation`

Graceful when B3 metadata (`provides`, `action_type`, `requires_env`,
`typical_use_case`, `cost_hint`) is absent — capability indexes simply
stay empty for that pack. As B3 tagging rolls out pack-by-pack, lookups
automatically start resolving without code changes.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class PackIndex:
    """In-memory index of all toolpack YAMLs under configured roots.

    Loaded at boot via `scan(roots)` then queried via lookup helpers.
    Safe to call `scan` multiple times — subsequent calls replace the
    prior index.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, dict[str, Any]] = {}
        # Inverse indexes — empty when B3 metadata absent on a pack.
        self._by_capability: dict[str, list[str]] = defaultdict(list)
        self._by_action_type: dict[str, list[str]] = defaultdict(list)
        self._by_use_case: dict[str, list[str]] = defaultdict(list)
        # Tool → owning pack (so discovery can locate a pack from a known tool uri)
        self._tool_to_pack: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def scan(self, roots: list[str | Path]) -> int:
        """Scan one or more directories for `*.yaml` pack manifests.

        Packs are identified by the presence of an `id` field that starts
        with `toolpack://` at the YAML top level. Other yaml files are
        silently ignored.

        Returns:
            Count of packs indexed. Re-scans replace the prior index.
        """
        self._by_id.clear()
        self._by_capability.clear()
        self._by_action_type.clear()
        self._by_use_case.clear()
        self._tool_to_pack.clear()

        count = 0
        for root in roots:
            root_path = Path(root)
            if not root_path.exists():
                continue
            for yaml_path in root_path.rglob("*.yaml"):
                try:
                    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
                except Exception as exc:
                    logger.debug("Skipping malformed pack yaml %s: %s", yaml_path, exc)
                    continue
                pack_id = data.get("id", "")
                if not isinstance(pack_id, str) or not pack_id.startswith("toolpack://"):
                    continue
                self._register_pack(pack_id, data)
                count += 1
        return count

    def _register_pack(self, pack_id: str, data: dict[str, Any]) -> None:
        """Write one pack into the primary + inverse indexes."""
        self._by_id[pack_id] = data

        # B3 metadata (all optional — packs without it still land in _by_id)
        for cap in _ensure_list(data.get("provides")):
            self._by_capability[cap].append(pack_id)

        action_type = data.get("action_type")
        if isinstance(action_type, str) and action_type:
            self._by_action_type[action_type].append(pack_id)

        for use_case in _ensure_list(data.get("typical_use_case")):
            self._by_use_case[use_case].append(pack_id)

        # Tool ownership — every tool's uri points back to its pack.
        for tool in data.get("tools", []) or []:
            tool_id = tool.get("id") if isinstance(tool, dict) else str(tool)
            if isinstance(tool_id, str) and tool_id:
                self._tool_to_pack[tool_id] = pack_id

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get_pack(self, pack_id: str) -> dict[str, Any] | None:
        """Return the full cached metadata for a pack, or None."""
        return self._by_id.get(pack_id)

    def all_packs(self) -> list[str]:
        """Return every indexed pack id, sorted for deterministic output."""
        return sorted(self._by_id.keys())

    def find_by_capability(self, capability: str) -> list[str]:
        """Return packs whose `provides:` includes the capability.

        Empty until B3 tagging reaches the pack in question. Callers should
        fall back to capability_map (B2) or direct pack id references.
        """
        return list(self._by_capability.get(capability, []))

    def find_by_action_type(self, action_type: str) -> list[str]:
        """Return packs tagged with the given `action_type` (B3)."""
        return list(self._by_action_type.get(action_type, []))

    def find_by_use_case(self, use_case: str) -> list[str]:
        """Return packs whose `typical_use_case:` includes the value (B3)."""
        return list(self._by_use_case.get(use_case, []))

    def find_tool(self, tool_uri: str) -> str | None:
        """Return the pack that owns a given `tool://...` uri, or None."""
        return self._tool_to_pack.get(tool_uri)

    def pack_count(self) -> int:
        return len(self._by_id)

    def tool_count(self) -> int:
        return len(self._tool_to_pack)

    # ------------------------------------------------------------------
    # Metadata snapshot (for ToolDiscoveryAgent prompt injection)
    # ------------------------------------------------------------------

    def metadata_snapshot(self) -> list[dict[str, Any]]:
        """Return a compact summary for prompt injection.

        Each entry has `{id, description, tool_count, provides, action_type,
        typical_use_case}`. Fields sourced from B3 metadata are always
        present with `[]` or `None` defaults when absent — so prompt
        templates can render a consistent shape.

        ToolDiscoveryAgent reads this snapshot instead of re-scanning the
        pack YAMLs on each genesis run.
        """
        snapshot: list[dict[str, Any]] = []
        for pack_id in sorted(self._by_id.keys()):
            data = self._by_id[pack_id]
            snapshot.append({
                "id": pack_id,
                "description": data.get("description", ""),
                "tool_count": len(data.get("tools", []) or []),
                "provides": _ensure_list(data.get("provides")),
                "action_type": data.get("action_type"),
                "typical_use_case": _ensure_list(data.get("typical_use_case")),
                "requires_env": _ensure_list(data.get("requires_env")),
                "cost_hint": data.get("cost_hint"),
            })
        return snapshot


# ── Helpers ────────────────────────────────────────────────────────────────


def _ensure_list(value: Any) -> list[str]:
    """Normalise a metadata field that may be a string, list, or absent."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return []


# ── Convenience boot helper ────────────────────────────────────────────────


def build_default_index() -> PackIndex:
    """Build a PackIndex from the shipped agent-tools install location.

    Tries `agent_tools.packs_root()` when the package exposes it, falls
    back to a conventional path guess. Returns an empty index when
    agent-tools is not installed.
    """
    index = PackIndex()
    candidate_roots: list[Path] = []
    try:
        import agent_tools
        packs_root = Path(agent_tools.__file__).parent / "packs"
        if packs_root.exists():
            candidate_roots.append(packs_root)
    except Exception as exc:
        logger.debug("agent_tools unavailable for PackIndex scan: %s", exc)
    # Allow cwd-relative packs for dev setups without the install
    for fallback in [Path("agent-tools/src/agent_tools/packs"), Path("packs")]:
        if fallback.exists():
            candidate_roots.append(fallback)
    if candidate_roots:
        index.scan(candidate_roots)  # type: ignore[arg-type]
    return index
