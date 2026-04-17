"""CapabilityResolver — declarative capability -> toolpack mapping (B2).

Reads `config/capabilities.yaml` and exposes lookup helpers that AgentArchitect v2
(B5, Phase 2d) and the runner (Phase 2e wiring) will consume to resolve toolpacks
per stage / capability.

Today this is config + loader only — no runtime integration into AgentRunner or
CompositionEngine. Domains without `industry:` skip capability resolution entirely.

Design notes:
- Empty-on-missing fallback: if `capabilities.yaml` is absent, all lookups return
  empty lists. Callers must not hard-depend on the file's existence.
- Idempotent caching: the resolver loads once per instance; pass `force_reload=True`
  to refresh when the file changes during a long-running process.
- Free-form names: capability and stage names are not enum-validated. Unknown
  names return [] rather than raising — keeps the map evolvable without
  breaking older consumers.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class CapabilityResolver:
    """Loads + resolves the capability map from `config/capabilities.yaml`."""

    def __init__(self, config_dir: str | Path | None = None) -> None:
        """Initialise. Resolution is lazy — file is read on first lookup.

        Args:
            config_dir: directory containing capabilities.yaml. Defaults to
                shadow-gentcore's top-level config/ (project_root/config/).
        """
        if config_dir is None:
            project_root = Path(__file__).resolve().parent.parent.parent
            config_dir = project_root / "config"
        self._config_path = Path(config_dir) / "capabilities.yaml"
        self._loaded: bool = False
        self._capabilities: dict[str, dict[str, Any]] = {}
        self._stage_defaults: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_packs(self, capability: str) -> list[str]:
        """Return the toolpack URIs bound to a capability, or [] if unknown.

        Args:
            capability: a capability name (e.g., 'cloud_query', 'observability').

        Returns:
            List of `toolpack://...` URIs. Empty if the capability is not in
            the map or the config file is missing.
        """
        self._ensure_loaded()
        entry = self._capabilities.get(capability)
        if not entry:
            return []
        packs = entry.get("packs", [])
        return [f"toolpack://{p}" for p in packs if isinstance(p, str)]

    def resolve_capabilities_for_stage(self, stage: str) -> list[str]:
        """Return the default capability list for a stage, or [] if unknown.

        Args:
            stage: a stage name (e.g., 'Triage', 'CodeWriter').

        Returns:
            List of capability names. Empty if the stage has no defaults.
        """
        self._ensure_loaded()
        return list(self._stage_defaults.get(stage, []))

    def resolve_packs_for_stage(self, stage: str) -> list[str]:
        """Flatten: stage -> capabilities -> deduplicated list of toolpack URIs.

        Order is preserved (capability order from stage_defaults; within a
        capability, pack order from capabilities.yaml). Duplicates are removed
        on first occurrence to keep deterministic output for snapshot tests.
        """
        seen: set[str] = set()
        ordered: list[str] = []
        for cap in self.resolve_capabilities_for_stage(stage):
            for pack_uri in self.resolve_packs(cap):
                if pack_uri not in seen:
                    seen.add(pack_uri)
                    ordered.append(pack_uri)
        return ordered

    def known_capabilities(self) -> list[str]:
        """All capability names declared in the map."""
        self._ensure_loaded()
        return list(self._capabilities.keys())

    def known_stages(self) -> list[str]:
        """All stage names declared in stage_defaults."""
        self._ensure_loaded()
        return list(self._stage_defaults.keys())

    def reload(self) -> None:
        """Force re-read of capabilities.yaml on next lookup."""
        self._loaded = False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._config_path.exists():
            logger.debug("capabilities.yaml not found at %s — capability resolution returns empty", self._config_path)
            return
        try:
            data = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            logger.warning("Failed to parse capabilities.yaml at %s: %s", self._config_path, exc)
            return
        caps = data.get("capabilities", {})
        if isinstance(caps, dict):
            self._capabilities = caps
        stage_defaults = data.get("stage_defaults", {})
        if isinstance(stage_defaults, dict):
            self._stage_defaults = {
                k: list(v) for k, v in stage_defaults.items() if isinstance(v, list)
            }
