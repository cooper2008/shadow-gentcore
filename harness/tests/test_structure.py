"""Tests for directory structure and required files."""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent

REQUIRED_DIRS = [
    "agents/_orchestrator",
    "agents/_maintenance",
    "agents/_genesis",
    "agents/_shared",
    "agents/_factory",
    "workflows/cross_domain",
    "workflows/maintenance",
    "workflows/genesis",
    "harness/core",
    "harness/core/modes",
    "harness/core/metrics",
    "harness/providers",
    "harness/tools",
    "harness/authoring",
    "harness/templates",
    "harness/bridges",
    "harness/cli",
    "harness/lints",
    "harness/tests",
    "harness/tests/fixtures/provider_recordings",
    "harness/tests/fixtures/manifests",
    "harness/tests/fixtures/workflows",
    "config/environments",
    "docs",
    "docs/references",
    "docs/templates",
    "tests/golden",
]

REQUIRED_FILES = [
    "CLAUDE.md",
    "README.md",
    "pyproject.toml",
    "Makefile",
    "config/categories.yaml",
    "config/domains.yaml",
    "config/rules.yaml",
    "config/workspace.yaml",
    "config/genesis_rules.yaml",
    "config/environments/local.yaml",
    "config/environments/cloud.yaml",
    "docs/SYSTEM_GUIDE.md",
    "docs/TEAM_GUIDE.md",
    "harness/cli/ai.py",
    "harness/tests/replay/recorder.py",
    "harness/tests/replay/player.py",
]


class TestDirectoryStructure:
    @pytest.mark.parametrize("dir_path", REQUIRED_DIRS)
    def test_required_dir_exists(self, dir_path: str) -> None:
        assert (REPO_ROOT / dir_path).is_dir(), f"Missing directory: {dir_path}"

    @pytest.mark.parametrize("file_path", REQUIRED_FILES)
    def test_required_file_exists(self, file_path: str) -> None:
        assert (REPO_ROOT / file_path).is_file(), f"Missing file: {file_path}"

    def test_claude_md_under_100_lines(self) -> None:
        # Cap grew 60→100 when memory tiers / source adapters / security
        # scanner / contract validator entered the "quick reference" block
        # in Q2 2026. Intent is still "not a 500-line dumping ground" —
        # detail belongs in SYSTEM_GUIDE / MEMORY_GUIDE / etc.
        content = (REPO_ROOT / "CLAUDE.md").read_text()
        lines = content.strip().split("\n")
        assert len(lines) <= 100, f"CLAUDE.md is {len(lines)} lines, must be ≤100 (move detail to dedicated docs)"
