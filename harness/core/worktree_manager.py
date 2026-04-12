"""WorktreeManager — provisions isolated worktrees per run with cleanup."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any


class WorktreeManager:
    """Manages isolated worktrees for agent runs.

    Each run gets its own temporary directory that is cleaned up on completion.
    Supports creating files, reading files, and full cleanup.
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base_dir = Path(base_dir) if base_dir else None
        self._active_worktrees: dict[str, Path] = {}

    def create(self, run_id: str) -> Path:
        """Provision an isolated worktree for a run.

        Args:
            run_id: Unique run identifier.

        Returns:
            Path to the worktree directory.
        """
        if run_id in self._active_worktrees:
            return self._active_worktrees[run_id]

        if self._base_dir:
            worktree = self._base_dir / run_id
            worktree.mkdir(parents=True, exist_ok=True)
        else:
            worktree = Path(tempfile.mkdtemp(prefix=f"worktree-{run_id}-"))

        self._active_worktrees[run_id] = worktree
        return worktree

    def write_file(self, run_id: str, relative_path: str, content: str) -> Path:
        """Write a file into a run's worktree.

        Args:
            run_id: The run identifier.
            relative_path: Path relative to worktree root.
            content: File content.

        Returns:
            Absolute path to the written file.
        """
        worktree = self._active_worktrees.get(run_id)
        if worktree is None:
            raise KeyError(f"No active worktree for run '{run_id}'")

        file_path = worktree / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return file_path

    def read_file(self, run_id: str, relative_path: str) -> str:
        """Read a file from a run's worktree."""
        worktree = self._active_worktrees.get(run_id)
        if worktree is None:
            raise KeyError(f"No active worktree for run '{run_id}'")

        file_path = worktree / relative_path
        return file_path.read_text(encoding="utf-8")

    def list_files(self, run_id: str) -> list[str]:
        """List all files in a run's worktree."""
        worktree = self._active_worktrees.get(run_id)
        if worktree is None:
            raise KeyError(f"No active worktree for run '{run_id}'")

        return [
            str(p.relative_to(worktree))
            for p in worktree.rglob("*")
            if p.is_file()
        ]

    def cleanup(self, run_id: str) -> None:
        """Clean up a run's worktree."""
        worktree = self._active_worktrees.pop(run_id, None)
        if worktree and worktree.exists():
            shutil.rmtree(worktree, ignore_errors=True)

    def cleanup_all(self) -> None:
        """Clean up all active worktrees."""
        for run_id in list(self._active_worktrees.keys()):
            self.cleanup(run_id)

    @property
    def active_runs(self) -> list[str]:
        return list(self._active_worktrees.keys())
