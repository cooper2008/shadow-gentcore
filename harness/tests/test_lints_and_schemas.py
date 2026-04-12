"""Tests for lint rules (tasks 103-106) and JSON schema exports (task 102)."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.lints.rules import ArchitectureLinter, LintResult

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ─── Doc-freshness lint (task 103) ────────────────────────────────────────────


class TestDocFreshnessLint:
    def test_required_docs_present(self) -> None:
        linter = ArchitectureLinter(PROJECT_ROOT)
        linter.lint_all()
        doc_results = [r for r in linter.results if r.rule == "doc_freshness"]
        assert any(r.passed for r in doc_results), "Expected at least one passing doc_freshness check"

    def test_missing_doc_fails(self, tmp_path: Path) -> None:
        linter = ArchitectureLinter(tmp_path)
        linter.lint_all()
        doc_results = [r for r in linter.results if r.rule == "doc_freshness"]
        assert any(not r.passed for r in doc_results), "Expected doc_freshness to fail for empty directory"

    def test_empty_doc_fails(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "ARCHITECTURE.md").touch()
        linter = ArchitectureLinter(tmp_path)
        linter.lint_all()
        doc_results = [r for r in linter.results if r.rule == "doc_freshness"]
        arch_results = [r for r in doc_results if "ARCHITECTURE" in r.message]
        assert any(not r.passed for r in arch_results)

    def test_non_empty_doc_passes(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "ARCHITECTURE.md").write_text("# Arch\nContent.", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Readme", encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text("# Agents", encoding="utf-8")
        linter = ArchitectureLinter(tmp_path)
        linter.lint_all()
        doc_results = [r for r in linter.results if r.rule == "doc_freshness"]
        assert all(r.passed for r in doc_results)


# ─── Dependency-direction lint (task 104) ─────────────────────────────────────


class TestDependencyDirectionLint:
    def test_no_forbidden_imports_in_harness(self) -> None:
        linter = ArchitectureLinter(PROJECT_ROOT)
        results = linter.lint_dependency_direction()
        failures = [r for r in results if not r.passed]
        assert failures == [], f"Forbidden cross-boundary imports found: {failures}"

    def test_forbidden_import_detected(self, tmp_path: Path) -> None:
        harness = tmp_path / "harness" / "core"
        harness.mkdir(parents=True)
        bad_file = harness / "bad_module.py"
        bad_file.write_text("import domain_backend.some_module\n", encoding="utf-8")

        linter = ArchitectureLinter(tmp_path)
        results = linter.lint_dependency_direction()
        failures = [r for r in results if not r.passed]
        assert any("domain_backend" in r.message for r in failures)

    def test_clean_harness_passes(self, tmp_path: Path) -> None:
        harness = tmp_path / "harness" / "core"
        harness.mkdir(parents=True)
        good_file = harness / "good_module.py"
        good_file.write_text("import os\nfrom pathlib import Path\n", encoding="utf-8")

        linter = ArchitectureLinter(tmp_path)
        results = linter.lint_dependency_direction()
        failures = [r for r in results if not r.passed]
        assert failures == []


# ─── Topology lint (task 105) ─────────────────────────────────────────────────


class TestTopologyLint:
    def test_maintenance_workflows_pass_topology(self) -> None:
        linter = ArchitectureLinter(PROJECT_ROOT)
        results = linter.lint_topology()
        failures = [r for r in results if not r.passed]
        assert failures == [], f"Topology failures: {failures}"

    def test_broken_topology_detected(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        bad_wf = wf_dir / "bad.yaml"
        bad_wf.write_text(
            "name: bad\ndomain: test\nsteps:\n"
            "  - name: step_a\n    depends_on: [nonexistent]\n",
            encoding="utf-8",
        )
        linter = ArchitectureLinter(tmp_path)
        results = linter.lint_topology(workflow_dirs=[wf_dir])
        failures = [r for r in results if not r.passed]
        assert any("nonexistent" in r.message for r in failures)

    def test_valid_topology_passes(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        good_wf = wf_dir / "good.yaml"
        good_wf.write_text(
            "name: good\ndomain: test\nsteps:\n"
            "  - name: a\n    depends_on: []\n"
            "  - name: b\n    depends_on: [a]\n",
            encoding="utf-8",
        )
        linter = ArchitectureLinter(tmp_path)
        results = linter.lint_topology(workflow_dirs=[wf_dir])
        failures = [r for r in results if not r.passed]
        assert failures == []


# ─── Schema-naming lint (task 106) ────────────────────────────────────────────


class TestSchemaNamingLint:
    def test_agent_contracts_pass_naming(self) -> None:
        contracts_root = PROJECT_ROOT.parent / "agent-contracts"
        if not contracts_root.exists():
            pytest.skip("agent-contracts repo not found")
        linter = ArchitectureLinter(PROJECT_ROOT)
        results = linter.lint_schema_naming()
        failures = [r for r in results if not r.passed]
        assert failures == [], f"Schema naming failures: {failures}"

    def test_bad_class_name_detected(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "agent-contracts" / "src" / "agent_contracts"
        contracts_dir.mkdir(parents=True)
        bad_file = contracts_dir / "bad_names.py"
        bad_file.write_text(
            "from pydantic import BaseModel\n"
            "class MyData(BaseModel): pass\n",
            encoding="utf-8",
        )
        linter = ArchitectureLinter(tmp_path / "shadow-gentcore")
        linter._root = tmp_path / "shadow-gentcore"
        linter._root.mkdir(exist_ok=True)
        results = linter.lint_schema_naming()
        failures = [r for r in results if not r.passed]
        assert any("MyData" in r.message for r in failures)


# ─── JSON Schema exports (task 102) ───────────────────────────────────────────


class TestSchemaExports:
    def test_export_schemas_creates_files(self, tmp_path: Path) -> None:
        import sys
        sys.path.insert(0, str(PROJECT_ROOT.parent / "agent-contracts" / "scripts"))

        from export_schemas import export_schemas, SCHEMAS
        export_schemas(tmp_path)

        for name in SCHEMAS:
            schema_file = tmp_path / f"{name}.json"
            assert schema_file.exists(), f"Missing schema: {name}.json"
            content = schema_file.read_text(encoding="utf-8")
            import json
            data = json.loads(content)
            assert "title" in data or "properties" in data, f"{name}.json has no schema content"

    def test_schema_count(self, tmp_path: Path) -> None:
        from export_schemas import SCHEMAS
        assert len(SCHEMAS) >= 10, "Expected at least 10 contract schemas"

    def test_agent_manifest_schema_has_properties(self, tmp_path: Path) -> None:
        from export_schemas import export_schemas
        export_schemas(tmp_path)
        import json
        data = json.loads((tmp_path / "agent_manifest.json").read_text(encoding="utf-8"))
        props = data.get("properties", {}) or data.get("$defs", {})
        assert len(props) > 0
