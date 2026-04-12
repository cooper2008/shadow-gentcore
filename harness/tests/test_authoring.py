"""Tests for authoring kit: Scaffolder, Validator, Certifier, CompatibilityRegistry, Publisher."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness.authoring.scaffolder import Scaffolder
from harness.authoring.validator import Validator, ValidationResult
from harness.authoring.certifier import Certifier, CertificationResult
from harness.authoring.compatibility import CompatibilityRegistry
from harness.authoring.publisher import Publisher


# ─── Scaffolder ───────────────────────────────────────────────────────────────


class TestScaffolder:
    def test_scaffold_domain(self, tmp_path: Path) -> None:
        s = Scaffolder()
        result = s.scaffold_domain("test_domain", tmp_path, owner="eng")

        domain_dir = tmp_path / "test_domain"
        assert domain_dir.is_dir()
        assert (domain_dir / "domain.yaml").exists()
        assert (domain_dir / "agents").is_dir()
        assert (domain_dir / "workflows").is_dir()
        assert len(result["files_created"]) == 1
        assert len(result["dirs_created"]) == 2

    def test_scaffold_domain_manifest_content(self, tmp_path: Path) -> None:
        s = Scaffolder()
        s.scaffold_domain("backend", tmp_path, owner="platform")
        content = (tmp_path / "backend" / "domain.yaml").read_text()
        assert "name: backend" in content
        assert "owner: platform" in content

    def test_scaffold_agent(self, tmp_path: Path) -> None:
        s = Scaffolder()
        s.scaffold_domain("mydom", tmp_path)
        domain_path = tmp_path / "mydom"
        result = s.scaffold_agent(domain_path, "MyAgent", version="v1", category="code_generation")

        agent_dir = domain_path / "agents" / "MyAgent" / "v1"
        assert agent_dir.is_dir()
        assert (agent_dir / "agent_manifest.yaml").exists()
        assert (agent_dir / "system_prompt.md").exists()
        assert len(result["files_created"]) == 2

    def test_scaffold_workflow(self, tmp_path: Path) -> None:
        s = Scaffolder()
        s.scaffold_domain("mydom", tmp_path)
        domain_path = tmp_path / "mydom"
        result = s.scaffold_workflow(domain_path, "ci_pipeline")

        wf_path = domain_path / "workflows" / "ci_pipeline.yaml"
        assert wf_path.exists()
        assert len(result["files_created"]) == 1

    def test_scaffold_pack(self, tmp_path: Path) -> None:
        s = Scaffolder()
        s.scaffold_domain("mydom", tmp_path)
        domain_path = tmp_path / "mydom"
        result = s.scaffold_pack(domain_path, "python_build")

        pack_dir = domain_path / "packs" / "python_build"
        assert pack_dir.is_dir()
        assert (pack_dir / "pack.yaml").exists()
        assert len(result["files_created"]) == 1


# ─── Validator ────────────────────────────────────────────────────────────────


class TestValidator:
    def _scaffold_valid_domain(self, tmp_path: Path) -> Path:
        s = Scaffolder()
        s.scaffold_domain("valid", tmp_path, owner="team")
        domain_path = tmp_path / "valid"
        s.scaffold_agent(domain_path, "AgentA", version="v1")
        s.scaffold_workflow(domain_path, "flow1")
        return domain_path

    def test_validate_good_domain(self, tmp_path: Path) -> None:
        domain_path = self._scaffold_valid_domain(tmp_path)
        v = Validator()
        result = v.validate_domain(domain_path)
        # Bare scaffold passes structural validation but may have policy warnings/errors
        # because the scaffolded workflow has only one step (no verify/review).
        # Filter out policy errors to check structural validity.
        structural_errors = [e for e in result.errors if not e["rule"].startswith("policy_")]
        assert not structural_errors, f"Structural errors: {structural_errors}"

    def test_validate_missing_manifest(self, tmp_path: Path) -> None:
        domain_path = tmp_path / "empty"
        domain_path.mkdir()
        v = Validator()
        result = v.validate_domain(domain_path)
        assert not result.is_valid
        assert any("domain.yaml not found" in e["message"] for e in result.errors)

    def test_validate_missing_required_fields(self, tmp_path: Path) -> None:
        domain_path = tmp_path / "bad"
        domain_path.mkdir()
        (domain_path / "domain.yaml").write_text("version: 1.0\n", encoding="utf-8")
        v = Validator()
        result = v.validate_domain(domain_path)
        assert not result.is_valid
        field_errors = [e for e in result.errors if e["rule"] == "required_field"]
        assert len(field_errors) == 3  # name, owner, purpose

    def test_validate_agent_manifest_missing_prompt(self, tmp_path: Path) -> None:
        domain_path = self._scaffold_valid_domain(tmp_path)
        # Delete the system prompt
        prompt = domain_path / "agents" / "AgentA" / "v1" / "system_prompt.md"
        prompt.unlink()
        v = Validator()
        result = v.validate_domain(domain_path)
        assert any("System prompt not found" in e["message"] for e in result.errors)

    def test_validate_workflow_topology_bad_dep(self, tmp_path: Path) -> None:
        domain_path = tmp_path / "topodom"
        domain_path.mkdir()
        (domain_path / "domain.yaml").write_text(
            "name: topodom\nowner: t\npurpose: test\n", encoding="utf-8"
        )
        (domain_path / "workflows").mkdir()
        (domain_path / "workflows" / "bad.yaml").write_text(
            "name: bad\ndomain: topodom\nsteps:\n"
            "  - name: s1\n    agent: a/b/v1\n    depends_on: [s_nonexistent]\n",
            encoding="utf-8",
        )
        v = Validator()
        result = v.validate_domain(domain_path)
        assert any("depends on unknown step" in e["message"] for e in result.errors)

    def test_validation_result_summary(self) -> None:
        r = ValidationResult()
        assert r.summary == "PASS: 0 errors, 0 warnings"
        r.add_error("x", "bad")
        assert r.summary.startswith("FAIL")


# ─── Certifier ────────────────────────────────────────────────────────────────


class TestCertifier:
    def test_certify_valid_domain(self, tmp_path: Path) -> None:
        s = Scaffolder()
        s.scaffold_domain("cert_dom", tmp_path, owner="eng")
        domain_path = tmp_path / "cert_dom"

        c = Certifier()
        result = c.certify_domain(domain_path)
        assert result.certified
        assert result.validation is not None and result.validation.is_valid
        assert result.dry_run_passed

    def test_certify_invalid_domain(self, tmp_path: Path) -> None:
        domain_path = tmp_path / "empty"
        domain_path.mkdir()
        c = Certifier()
        result = c.certify_domain(domain_path)
        assert not result.certified

    def test_certify_with_dry_run_fn(self, tmp_path: Path) -> None:
        s = Scaffolder()
        s.scaffold_domain("dr_dom", tmp_path)
        domain_path = tmp_path / "dr_dom"

        c = Certifier()
        result = c.certify_domain(
            domain_path, dry_run_fn=lambda p: {"success": True}
        )
        assert result.dry_run_passed

    def test_certify_dry_run_failure(self, tmp_path: Path) -> None:
        s = Scaffolder()
        s.scaffold_domain("fail_dom", tmp_path)
        domain_path = tmp_path / "fail_dom"

        c = Certifier()
        result = c.certify_domain(
            domain_path, dry_run_fn=lambda p: {"success": False, "error": "timeout"}
        )
        assert not result.dry_run_passed
        assert not result.certified

    def test_certification_summary(self) -> None:
        r = CertificationResult()
        assert "NOT CERTIFIED" in r.summary


# ─── CompatibilityRegistry ───────────────────────────────────────────────────


class TestCompatibilityRegistry:
    def test_compatible_schemas(self) -> None:
        reg = CompatibilityRegistry()
        reg.register_schema("TaskOutput", "1.0", ["code", "tests"])
        reg.register_schema("TaskOutput", "1.1", ["code", "tests", "docs"])
        result = reg.check_compatibility("TaskOutput", "1.0", "1.1")
        assert result["compatible"] is True
        assert result["added_fields"] == ["docs"]
        assert result["removed_fields"] == []

    def test_breaking_change(self) -> None:
        reg = CompatibilityRegistry()
        reg.register_schema("TaskOutput", "1.0", ["code", "tests", "review"])
        reg.register_schema("TaskOutput", "2.0", ["code", "docs"])
        result = reg.check_compatibility("TaskOutput", "1.0", "2.0")
        assert result["compatible"] is False
        assert result["breaking"] is True
        assert "review" in result["removed_fields"]
        assert "tests" in result["removed_fields"]

    def test_unknown_schema(self) -> None:
        reg = CompatibilityRegistry()
        result = reg.check_compatibility("Unknown", "1.0", "2.0")
        assert result["compatible"] is False
        assert "error" in result

    def test_port_compatibility(self) -> None:
        reg = CompatibilityRegistry()
        reg.register_port("codegen_out", "CodeOutput", direction="output")
        reg.register_port("validate_in", "CodeOutput", direction="input")
        result = reg.check_port_compatibility("codegen_out", "validate_in")
        assert result["compatible"] is True

    def test_port_incompatible_direction(self) -> None:
        reg = CompatibilityRegistry()
        reg.register_port("a_out", "Schema", direction="output")
        reg.register_port("b_out", "Schema", direction="output")
        result = reg.check_port_compatibility("a_out", "b_out")
        assert result["compatible"] is False

    def test_port_incompatible_schema(self) -> None:
        reg = CompatibilityRegistry()
        reg.register_port("a_out", "SchemaA", direction="output")
        reg.register_port("b_in", "SchemaB", direction="input")
        result = reg.check_port_compatibility("a_out", "b_in")
        assert result["compatible"] is False

    def test_registered_schemas(self) -> None:
        reg = CompatibilityRegistry()
        reg.register_schema("A", "1.0", ["x"])
        assert "A@1.0" in reg.registered_schemas


# ─── Publisher ────────────────────────────────────────────────────────────────


class TestPublisher:
    def test_publish_and_discover(self, tmp_path: Path) -> None:
        catalog_dir = tmp_path / "catalog"
        pub = Publisher(catalog_dir=catalog_dir)

        domain_path = tmp_path / "my_domain"
        domain_path.mkdir()

        result = pub.publish(domain_path, version="1.0.0", owner="eng", certification_summary="PASS")
        assert (catalog_dir / "my_domain@1.0.0.json").exists()

        entries = pub.discover("my_domain")
        assert len(entries) == 1
        assert entries[0]["version"] == "1.0.0"

    def test_discover_all(self, tmp_path: Path) -> None:
        catalog_dir = tmp_path / "catalog"
        pub = Publisher(catalog_dir=catalog_dir)

        pub.publish(tmp_path / "a", version="1.0.0", owner="x")
        pub.publish(tmp_path / "b", version="1.0.0", owner="y")

        entries = pub.discover()
        assert len(entries) == 2

    def test_get_latest_version(self, tmp_path: Path) -> None:
        catalog_dir = tmp_path / "catalog"
        pub = Publisher(catalog_dir=catalog_dir)

        pub.publish(tmp_path / "dom", version="1.0.0", owner="x")
        pub.publish(tmp_path / "dom", version="1.1.0", owner="x")

        assert pub.get_latest_version("dom") == "1.1.0"

    def test_get_latest_version_unknown(self, tmp_path: Path) -> None:
        catalog_dir = tmp_path / "catalog"
        pub = Publisher(catalog_dir=catalog_dir)
        assert pub.get_latest_version("nonexistent") is None

    def test_index_updated(self, tmp_path: Path) -> None:
        catalog_dir = tmp_path / "catalog"
        pub = Publisher(catalog_dir=catalog_dir)
        pub.publish(tmp_path / "d", version="1.0.0", owner="x")

        index = json.loads((catalog_dir / "index.json").read_text())
        assert "d" in index["domains"]
        assert "1.0.0" in index["domains"]["d"]["versions"]
