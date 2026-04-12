"""Cross-domain workflow support — domain discovery and cross-domain port resolution."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class DomainRegistry:
    """Discovers and registers domains from config/domains.yaml and filesystem paths.

    Supports:
    - Path-based discovery (scan directories for domain.yaml manifests)
    - Explicit registry entries
    - Cross-domain port resolution
    """

    def __init__(self) -> None:
        self._domains: dict[str, dict[str, Any]] = {}
        self._port_map: dict[str, dict[str, Any]] = {}

    # ── Discovery ────────────────────────────────────────────────────────

    def load_from_config(self, config_path: str | Path) -> None:
        """Load domain discovery config and register all discovered domains."""
        config_path = Path(config_path)
        if not config_path.exists():
            logger.warning("Domain config not found: %s", config_path)
            return

        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        discovery = data.get("discovery", {})

        for scan_path in discovery.get("paths", []):
            self.discover_path(Path(scan_path))

        registry = data.get("registry", {}) or {}
        for domain_name, entry in registry.items():
            if entry.get("enabled", True):
                domain_path = Path(entry["path"])
                self._load_domain_at(domain_name, domain_path)

    def discover_path(self, base_path: Path) -> list[str]:
        """Scan a directory for domain.yaml manifests and register each."""
        discovered: list[str] = []
        if not base_path.exists():
            return discovered

        # Check if base_path itself is a domain
        manifest = base_path / "domain.yaml"
        if manifest.exists():
            name = self._load_domain_at(base_path.name, base_path)
            if name:
                discovered.append(name)
        else:
            # Scan subdirectories
            for child in base_path.iterdir():
                if child.is_dir() and (child / "domain.yaml").exists():
                    name = self._load_domain_at(child.name, child)
                    if name:
                        discovered.append(name)

        return discovered

    def _load_domain_at(self, name: str, path: Path) -> str | None:
        """Load a domain manifest from path and register it."""
        manifest_path = path / "domain.yaml"
        if not manifest_path.exists():
            return None

        try:
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            domain_name = data.get("name", name)
            self._domains[domain_name] = {
                "name": domain_name,
                "path": str(path),
                "manifest": data,
            }
            # Index ports
            for port in data.get("ports", []):
                port_id = f"{domain_name}.{port.get('name', '')}"
                self._port_map[port_id] = {
                    "domain": domain_name,
                    "port": port,
                }
            logger.debug("Registered domain: %s at %s", domain_name, path)
            return domain_name
        except Exception as exc:
            logger.warning("Failed to load domain at %s: %s", path, exc)
            return None

    def register_domain(self, name: str, path: str | Path, manifest: dict[str, Any] | None = None) -> None:
        """Manually register a domain."""
        path = Path(path)
        entry: dict[str, Any] = {"name": name, "path": str(path), "manifest": manifest or {}}
        self._domains[name] = entry
        for port in (manifest or {}).get("ports", []):
            port_id = f"{name}.{port.get('name', '')}"
            self._port_map[port_id] = {"domain": name, "port": port}

    # ── Port Resolution ──────────────────────────────────────────────────

    def resolve_port(self, port_ref: str) -> dict[str, Any] | None:
        """Resolve a cross-domain port reference (format: 'domain.port_name').

        Returns port metadata or None if not found.
        """
        return self._port_map.get(port_ref)

    def resolve_cross_domain_connection(
        self, source_ref: str, target_ref: str
    ) -> dict[str, Any]:
        """Validate a cross-domain port connection.

        Returns compatibility result with source, target, and compatible flag.
        """
        source = self.resolve_port(source_ref)
        target = self.resolve_port(target_ref)

        if source is None:
            return {"compatible": False, "error": f"Source port not found: {source_ref}"}
        if target is None:
            return {"compatible": False, "error": f"Target port not found: {target_ref}"}

        src_direction = source["port"].get("direction", "output")
        tgt_direction = target["port"].get("direction", "input")
        src_schema = source["port"].get("schema", "")
        tgt_schema = target["port"].get("schema", "")

        if src_direction != "output":
            return {"compatible": False, "error": f"Source port must be output, got: {src_direction}"}
        if tgt_direction != "input":
            return {"compatible": False, "error": f"Target port must be input, got: {tgt_direction}"}
        if src_schema and tgt_schema and src_schema != tgt_schema:
            return {
                "compatible": False,
                "error": f"Schema mismatch: {src_schema} vs {tgt_schema}",
            }

        return {
            "compatible": True,
            "source": source_ref,
            "target": target_ref,
            "source_domain": source["domain"],
            "target_domain": target["domain"],
        }

    # ── Accessors ────────────────────────────────────────────────────────

    @property
    def domains(self) -> dict[str, dict[str, Any]]:
        return dict(self._domains)

    @property
    def port_map(self) -> dict[str, dict[str, Any]]:
        return dict(self._port_map)

    def get_domain(self, name: str) -> dict[str, Any] | None:
        return self._domains.get(name)
