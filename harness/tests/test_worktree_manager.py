"""Tests for WorktreeManager."""

from __future__ import annotations

import tempfile

import pytest

from harness.core.worktree_manager import WorktreeManager


class TestWorktreeManager:
    def test_create_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            wm = WorktreeManager(base_dir=tmpdir)
            path = wm.create("run-1")
            assert path.exists()
            assert path.is_dir()
            assert "run-1" in wm.active_runs

    def test_create_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            wm = WorktreeManager(base_dir=tmpdir)
            p1 = wm.create("run-1")
            p2 = wm.create("run-1")
            assert p1 == p2

    def test_write_and_read_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            wm = WorktreeManager(base_dir=tmpdir)
            wm.create("run-1")
            wm.write_file("run-1", "src/main.py", "print('hello')")
            content = wm.read_file("run-1", "src/main.py")
            assert content == "print('hello')"

    def test_list_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            wm = WorktreeManager(base_dir=tmpdir)
            wm.create("run-1")
            wm.write_file("run-1", "a.py", "a")
            wm.write_file("run-1", "b/c.py", "c")
            files = wm.list_files("run-1")
            assert len(files) == 2

    def test_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            wm = WorktreeManager(base_dir=tmpdir)
            path = wm.create("run-1")
            wm.write_file("run-1", "test.txt", "data")
            wm.cleanup("run-1")
            assert not path.exists()
            assert "run-1" not in wm.active_runs

    def test_cleanup_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            wm = WorktreeManager(base_dir=tmpdir)
            wm.create("run-1")
            wm.create("run-2")
            assert len(wm.active_runs) == 2
            wm.cleanup_all()
            assert len(wm.active_runs) == 0

    def test_write_file_missing_worktree(self) -> None:
        wm = WorktreeManager()
        with pytest.raises(KeyError, match="No active worktree"):
            wm.write_file("nonexistent", "f.txt", "data")

    def test_read_file_missing_worktree(self) -> None:
        wm = WorktreeManager()
        with pytest.raises(KeyError, match="No active worktree"):
            wm.read_file("nonexistent", "f.txt")

    def test_temp_dir_fallback(self) -> None:
        wm = WorktreeManager()  # no base_dir, uses tempfile
        path = wm.create("run-tmp")
        assert path.exists()
        wm.cleanup("run-tmp")
