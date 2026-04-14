"""Durable workflow state — checkpoint and resume DAG execution."""
from __future__ import annotations

import fcntl
import json
import os
import threading
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
        """Persist a completed step result to disk.

        Uses a per-caller unique .tmp file (pid + thread-id suffix) with an
        exclusive flock, followed by an atomic rename onto the final path.
        This means concurrent writers each have their own tmp file so they
        never collide; the last rename wins, which is safe because rename(2)
        is atomic on POSIX.  Readers always see either the old complete JSON
        file or the new complete JSON file — never a partial write.
        """
        path = self._step_path(workflow_id, step_name)
        data = {"step": step_name, "timestamp": time.time(), "result": result}
        content = json.dumps(data, indent=2, default=str)
        # Use pid+thread-id so concurrent writers don't share the same tmp file.
        unique_suffix = f".{os.getpid()}.{threading.get_ident()}.tmp"
        tmp_path = path.with_name(path.name + unique_suffix)
        with open(tmp_path, "w", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.rename(path)  # atomic on POSIX

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
        """Delete all persisted state for a workflow.

        Acquires an exclusive lock on each file before unlinking to avoid
        racing with a concurrent ``save_step`` that is mid-write.
        """
        d = self._base / workflow_id
        if d.exists():
            for p in list(d.glob("*.json")) + list(d.glob("*.tmp*")):
                try:
                    with open(p, "r+", encoding="utf-8") as fh:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                        p.unlink()
                except FileNotFoundError:
                    pass  # already removed by a concurrent clear
            try:
                d.rmdir()
            except OSError:
                pass  # directory may still contain files from a racing writer
