"""Agent memory — persistent key-value store scoped per agent+domain."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MemoryStore(Protocol):
    """Protocol for agent long-term memory."""

    def store(
        self,
        agent_id: str,
        key: str,
        value: str,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...

    def recall(
        self,
        agent_id: str,
        key: str | None = None,
        k: int = 5,
    ) -> list[dict[str, Any]]: ...

    def clear(self, agent_id: str) -> None: ...


class FileMemoryStore:
    """File-based memory store — JSON lines files in .gentcore/memory/{agent_id}/."""

    def __init__(self, base_dir: str | Path = ".gentcore/memory") -> None:
        self._base = Path(base_dir)

    def _agent_dir(self, agent_id: str) -> Path:
        safe_id = agent_id.replace("/", "_")
        d = self._base / safe_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def store(
        self,
        agent_id: str,
        key: str,
        value: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append a memory entry for agent_id to its JSONL file."""
        d = self._agent_dir(agent_id)
        entry: dict[str, Any] = {
            "key": key,
            "value": value,
            "timestamp": time.time(),
            "metadata": metadata or {},
        }
        with open(d / "memories.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def recall(
        self,
        agent_id: str,
        key: str | None = None,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        """Return the most recent k memory entries for agent_id, optionally filtered by key."""
        d = self._agent_dir(agent_id)
        memories_file = d / "memories.jsonl"
        if not memories_file.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in memories_file.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                if key is None or entry.get("key") == key:
                    entries.append(entry)
            except json.JSONDecodeError:
                continue
        return entries[-k:]

    def clear(self, agent_id: str) -> None:
        """Remove all stored memories for agent_id."""
        d = self._agent_dir(agent_id)
        memories_file = d / "memories.jsonl"
        if memories_file.exists():
            memories_file.unlink()


class InMemoryMemoryStore:
    """In-memory store — suitable for testing and ephemeral runs."""

    def __init__(self) -> None:
        self._data: dict[str, list[dict[str, Any]]] = {}

    def store(
        self,
        agent_id: str,
        key: str,
        value: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._data.setdefault(agent_id, []).append({
            "key": key,
            "value": value,
            "timestamp": time.time(),
            "metadata": metadata or {},
        })

    def recall(
        self,
        agent_id: str,
        key: str | None = None,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        entries = self._data.get(agent_id, [])
        if key is not None:
            entries = [e for e in entries if e.get("key") == key]
        return entries[-k:]

    def clear(self, agent_id: str) -> None:
        self._data.pop(agent_id, None)
