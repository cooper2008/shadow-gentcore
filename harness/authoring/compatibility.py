"""CompatibilityRegistry — tracks schema versions and port compatibility."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class CompatibilityRegistry:
    """Tracks schema versions and detects breaking changes in ports/contracts.

    Maintains a registry of versioned schemas and port definitions,
    allowing detection of incompatible changes between versions.
    """

    def __init__(self) -> None:
        self._schemas: dict[str, dict[str, Any]] = {}
        self._port_registry: dict[str, dict[str, Any]] = {}

    def register_schema(self, name: str, version: str, fields: list[str]) -> None:
        """Register a schema version with its field list."""
        key = f"{name}@{version}"
        self._schemas[key] = {
            "name": name,
            "version": version,
            "fields": set(fields),
        }
        logger.debug("Registered schema %s", key)

    def register_port(self, name: str, schema_name: str, direction: str = "input") -> None:
        """Register a port with its associated schema."""
        self._port_registry[name] = {
            "schema_name": schema_name,
            "direction": direction,
        }

    def check_compatibility(
        self, name: str, old_version: str, new_version: str
    ) -> dict[str, Any]:
        """Check compatibility between two schema versions.

        Returns:
            Dict with 'compatible', 'added_fields', 'removed_fields', 'breaking'.
        """
        old_key = f"{name}@{old_version}"
        new_key = f"{name}@{new_version}"

        if old_key not in self._schemas:
            return {"error": f"Schema {old_key} not found", "compatible": False, "breaking": True}
        if new_key not in self._schemas:
            return {"error": f"Schema {new_key} not found", "compatible": False, "breaking": True}

        old_fields = self._schemas[old_key]["fields"]
        new_fields = self._schemas[new_key]["fields"]

        added = new_fields - old_fields
        removed = old_fields - new_fields

        # Breaking change = fields removed (consumers may depend on them)
        breaking = len(removed) > 0

        return {
            "compatible": not breaking,
            "breaking": breaking,
            "added_fields": sorted(added),
            "removed_fields": sorted(removed),
            "old_version": old_version,
            "new_version": new_version,
        }

    def check_port_compatibility(self, port_a: str, port_b: str) -> dict[str, Any]:
        """Check if two ports are compatible for connection."""
        if port_a not in self._port_registry:
            return {"compatible": False, "error": f"Port '{port_a}' not registered"}
        if port_b not in self._port_registry:
            return {"compatible": False, "error": f"Port '{port_b}' not registered"}

        a = self._port_registry[port_a]
        b = self._port_registry[port_b]

        # Ports are compatible if one is input and one is output with same schema
        same_schema = a["schema_name"] == b["schema_name"]
        direction_ok = a["direction"] != b["direction"]

        return {
            "compatible": same_schema and direction_ok,
            "same_schema": same_schema,
            "direction_compatible": direction_ok,
        }

    @property
    def registered_schemas(self) -> list[str]:
        return list(self._schemas.keys())

    @property
    def registered_ports(self) -> list[str]:
        return list(self._port_registry.keys())
