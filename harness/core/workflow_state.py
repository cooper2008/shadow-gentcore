"""Durable workflow state — checkpoint and resume DAG execution."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class WorkflowStateStore(Protocol):
    """Protocol for persisting workflow step results."""

    def save_step(self, workflow_id: str, step_name: str, result: dict[str, Any]) -> None: ...
    def load_step(self, workflow_id: str, step_name: str) -> dict[str, Any] | None: ...
    def list_completed(self, workflow_id: str) -> list[str]: ...
    def clear(self, workflow_id: str) -> None: ...


class InMemoryStateStore:
    """Default in-memory store (current behavior, no durability)."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, dict[str, Any]]] = {}

    def save_step(self, workflow_id: str, step_name: str, result: dict[str, Any]) -> None:
        self._store.setdefault(workflow_id, {})[step_name] = result

    def load_step(self, workflow_id: str, step_name: str) -> dict[str, Any] | None:
        return self._store.get(workflow_id, {}).get(step_name)

    def list_completed(self, workflow_id: str) -> list[str]:
        return list(self._store.get(workflow_id, {}).keys())

    def clear(self, workflow_id: str) -> None:
        self._store.pop(workflow_id, None)


class FileStateStore:
    """Persist workflow state to JSON files in a directory.

    Each step result is written as an individual JSON file under
    ``<base_dir>/<workflow_id>/<step_name>.json``.  This means a
    server crash mid-DAG only loses the in-flight step; all
    previously completed steps survive and the workflow can resume.

    Args:
        base_dir: Root directory for state files.  Defaults to
            ``.gentcore/state`` relative to the current working directory.
    """

    def __init__(self, base_dir: str | Path = ".gentcore/state") -> None:
        self._base = Path(base_dir)

    # ── internal helpers ──────────────────────────────────────────────────

    def _step_path(self, workflow_id: str, step_name: str) -> Path:
        d = self._base / workflow_id
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{step_name}.json"

    # ── WorkflowStateStore interface ──────────────────────────────────────

    def save_step(self, workflow_id: str, step_name: str, result: dict[str, Any]) -> None:
        """Persist a completed step result to disk."""
        path = self._step_path(workflow_id, step_name)
        data = {"step": step_name, "timestamp": time.time(), "result": result}
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def load_step(self, workflow_id: str, step_name: str) -> dict[str, Any] | None:
        """Return the saved result for a step, or None if not found."""
        path = self._base / workflow_id / f"{step_name}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("result")

    def list_completed(self, workflow_id: str) -> list[str]:
        """Return step names that have been persisted, sorted by filename."""
        d = self._base / workflow_id
        if not d.exists():
            return []
        return [p.stem for p in sorted(d.glob("*.json"))]

    def clear(self, workflow_id: str) -> None:
        """Delete all persisted state for a workflow."""
        d = self._base / workflow_id
        if d.exists():
            for f in d.glob("*.json"):
                f.unlink()
            d.rmdir()
