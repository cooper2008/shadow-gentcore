"""Tests for architecture lint rules."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from harness.lints.rules import ArchitectureLinter, LintResult


class TestArchitectureLinter:
    def test_lint_project_root(self) -> None:
        """Lint the actual project root — should pass directory structure checks."""
        linter = ArchitectureLinter(project_root=Path(__file__).resolve().parents[2])
        results = linter.lint_all()
        dir_results = [r for r in results if r.rule == "directory_structure"]
        assert all(r.passed for r in dir_results)

    def test_lint_missing_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            linter = ArchitectureLinter(project_root=tmpdir)
            results = linter.lint_all()
            dir_fails = [r for r in results if r.rule == "directory_structure" and not r.passed]
            assert len(dir_fails) > 0

    def test_doc_freshness_present(self) -> None:
        linter = ArchitectureLinter(project_root=Path(__file__).resolve().parents[2])
        results = linter.lint_all()
        doc_results = [r for r in results if r.rule == "doc_freshness"]
        assert any(r.passed for r in doc_results)

    def test_doc_freshness_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            linter = ArchitectureLinter(project_root=tmpdir)
            results = linter.lint_all()
            doc_fails = [r for r in results if r.rule == "doc_freshness" and not r.passed]
            assert len(doc_fails) > 0

    def test_manifest_presence_with_domain(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            examples = Path(tmpdir) / "examples" / "backend"
            examples.mkdir(parents=True)
            (examples / "domain.yaml").write_text("name: backend\n", encoding="utf-8")
            linter = ArchitectureLinter(project_root=tmpdir)
            results = linter.lint_all()
            manifest_pass = [r for r in results if r.rule == "manifest_presence" and r.passed]
            assert len(manifest_pass) == 1

    def test_manifest_presence_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            examples = Path(tmpdir) / "examples" / "backend"
            examples.mkdir(parents=True)
            linter = ArchitectureLinter(project_root=tmpdir)
            results = linter.lint_all()
            manifest_fail = [r for r in results if r.rule == "manifest_presence" and not r.passed]
            assert len(manifest_fail) == 1

    def test_passed_property(self) -> None:
        linter = ArchitectureLinter(project_root=Path(__file__).resolve().parents[2])
        linter.lint_all()
        # Real project may have some warnings but check property works
        assert isinstance(linter.passed, bool)

    def test_failure_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            linter = ArchitectureLinter(project_root=tmpdir)
            linter.lint_all()
            assert linter.failure_count > 0
