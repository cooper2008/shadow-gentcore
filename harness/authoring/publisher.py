"""Publisher — publishes certified domains to repo-local catalog."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class Publisher:
    """Publishes certified domains to a repo-local catalog.

    Records version, ownership, certification evidence, and
    makes the domain discoverable via the catalog index.
    """

    def __init__(self, catalog_dir: Path | None = None) -> None:
        self._catalog_dir = catalog_dir or Path("catalog")

    def publish(
        self,
        domain_path: Path,
        version: str,
        owner: str,
        certification_summary: str = "",
    ) -> dict[str, Any]:
        """Publish a domain to the catalog.

        Args:
            domain_path: Path to the certified domain.
            version: Semantic version string.
            owner: Team or individual owner.
            certification_summary: Summary from certification.

        Returns:
            Dict with publish metadata.
        """
        domain_name = domain_path.name
        self._catalog_dir.mkdir(parents=True, exist_ok=True)

        entry = {
            "domain": domain_name,
            "version": version,
            "owner": owner,
            "source_path": str(domain_path),
            "certification": certification_summary,
            "published_at": datetime.now().isoformat(),
        }

        # Write entry
        entry_path = self._catalog_dir / f"{domain_name}@{version}.json"
        entry_path.write_text(json.dumps(entry, indent=2), encoding="utf-8")

        # Update index
        self._update_index(domain_name, version)

        logger.info("Published %s@%s to catalog", domain_name, version)
        return {
            "entry_path": str(entry_path),
            "domain": domain_name,
            "version": version,
        }

    def _update_index(self, domain_name: str, version: str) -> None:
        """Update the catalog index with the new entry."""
        index_path = self._catalog_dir / "index.json"
        if index_path.exists():
            index = json.loads(index_path.read_text(encoding="utf-8"))
        else:
            index = {"domains": {}}

        if domain_name not in index["domains"]:
            index["domains"][domain_name] = {"versions": []}

        versions = index["domains"][domain_name]["versions"]
        if version not in versions:
            versions.append(version)

        index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

    def discover(self, domain_name: str | None = None) -> list[dict[str, Any]]:
        """Discover published domains from the catalog.

        Args:
            domain_name: Filter by domain name. None returns all.

        Returns:
            List of catalog entries.
        """
        if not self._catalog_dir.exists():
            return []

        entries = []
        for entry_file in self._catalog_dir.glob("*.json"):
            if entry_file.name == "index.json":
                continue
            entry = json.loads(entry_file.read_text(encoding="utf-8"))
            if domain_name is None or entry.get("domain") == domain_name:
                entries.append(entry)

        return sorted(entries, key=lambda e: e.get("published_at", ""))

    def get_latest_version(self, domain_name: str) -> str | None:
        """Get the latest published version of a domain."""
        index_path = self._catalog_dir / "index.json"
        if not index_path.exists():
            return None

        index = json.loads(index_path.read_text(encoding="utf-8"))
        versions = index.get("domains", {}).get(domain_name, {}).get("versions", [])
        return versions[-1] if versions else None
