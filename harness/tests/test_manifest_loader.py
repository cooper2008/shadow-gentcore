"""Tests for ManifestLoader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.core.manifest_loader import ManifestLoader


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def tmp_domain(tmp_path: Path) -> Path:
    """Create a minimal domain layout in a temp directory."""
    # domain.yaml
    domain = {
        "name": "test_domain",
        "owner": "test-team",
        "purpose": "Testing",
        "context_files": ["context/standards.md"],
    }
    _write(tmp_path / "domain.yaml", yaml.dump(domain))

    # context/standards.md
    _write(tmp_path / "context/standards.md", "# Standards\n- Use type hints\n")

    # Agent
    manifest = {
        "id": "test_domain/TestAgent/v1",
        "domain": "test_domain",
        "category": "reasoning",
        "execution_mode": {"primary": "chain_of_thought"},
        "tools": [],
        "permissions": {},
        "system_prompt_ref": "system_prompt.md",
    }
    agent_dir = tmp_path / "agents" / "TestAgent" / "v1"
    _write(agent_dir / "agent_manifest.yaml", yaml.dump(manifest))
    _write(agent_dir / "system_prompt.md", "You are TestAgent.")

    # Workflow
    workflow = {
        "name": "test_workflow",
        "domain": "test_domain",
        "steps": [
            {"name": "step1", "agent": "test_domain/TestAgent/v1", "description": "Test step"},
        ],
        "budget": {"max_tokens": 10000},
    }
    _write(tmp_path / "workflows" / "test_workflow.yaml", yaml.dump(workflow))

    return tmp_path


class TestManifestLoader:
    def test_load_yaml(self, tmp_domain: Path) -> None:
        loader = ManifestLoader()
        data = loader.load_yaml(tmp_domain / "domain.yaml")
        assert data["name"] == "test_domain"

    def test_load_domain(self, tmp_domain: Path) -> None:
        loader = ManifestLoader()
        domain = loader.load_domain(tmp_domain)
        assert domain["owner"] == "test-team"
        assert "context_files" in domain

    def test_load_agent_with_context(self, tmp_domain: Path) -> None:
        loader = ManifestLoader()
        domain = loader.load_domain(tmp_domain)
        agent_dir = tmp_domain / "agents" / "TestAgent" / "v1"
        manifest, system_prompt, context_items = loader.load_agent(
            agent_dir, tmp_domain, domain
        )
        assert manifest["id"] == "test_domain/TestAgent/v1"
        assert "TestAgent" in system_prompt
        assert len(context_items) == 1
        assert "standards" in context_items[0]["source"]
        assert context_items[0]["priority"] == 10

    def test_load_workflow(self, tmp_domain: Path) -> None:
        loader = ManifestLoader()
        wf = loader.load_workflow(tmp_domain / "workflows" / "test_workflow.yaml")
        assert wf["name"] == "test_workflow"
        assert len(wf["steps"]) == 1

    def test_build_step_configs(self, tmp_domain: Path) -> None:
        loader = ManifestLoader()
        domain = loader.load_domain(tmp_domain)
        wf = loader.load_workflow(tmp_domain / "workflows" / "test_workflow.yaml")
        task_input = {"instruction": "Do something"}
        configs = loader.build_step_configs(wf, tmp_domain, domain, task_input)
        assert "step1" in configs
        cfg = configs["step1"]
        assert cfg["manifest"]["id"] == "test_domain/TestAgent/v1"
        assert "TestAgent" in cfg["system_prompt"]
        assert cfg["task"]["instruction"] == "Do something"
        assert any("standards" in c["source"] for c in cfg["context_items"])

    def test_boot_engine(self, tmp_domain: Path) -> None:
        loader = ManifestLoader()
        engine, workflow, step_configs = loader.boot_engine(
            tmp_domain / "workflows" / "test_workflow.yaml",
            domain_root=tmp_domain,
        )
        assert workflow["name"] == "test_workflow"
        assert "step1" in step_configs
        assert engine is not None


class TestIndustryRegistry:
    """B4 — industry field on DomainManifest + config/industries.yaml registry."""

    def test_industry_roundtrip_through_load_domain(self, tmp_path: Path) -> None:
        """Domain.yaml industry field is preserved through load_domain."""
        domain = {
            "name": "acme-ops",
            "owner": "sre",
            "purpose": "Incident response",
            "industry": "aws-ops",
        }
        _write(tmp_path / "domain.yaml", yaml.dump(domain))
        loader = ManifestLoader()
        result = loader.load_domain(tmp_path)
        assert result["industry"] == "aws-ops"

    def test_load_domain_without_industry(self, tmp_path: Path) -> None:
        """Domain.yaml without industry is still valid — field is optional."""
        domain = {"name": "backend", "owner": "team", "purpose": "test"}
        _write(tmp_path / "domain.yaml", yaml.dump(domain))
        loader = ManifestLoader()
        result = loader.load_domain(tmp_path)
        assert result.get("industry") is None

    def test_load_industries_from_project_config(self) -> None:
        """Default load_industries() reads shadow-gentcore's config/industries.yaml."""
        industries = ManifestLoader.load_industries()
        assert "aws-ops" in industries
        assert "generic" in industries
        entry = industries["aws-ops"]
        assert "description" in entry
        assert "typical_stages" in entry
        assert "typical_packs" in entry

    def test_load_industries_missing_file(self, tmp_path: Path) -> None:
        """load_industries returns empty dict when file is absent (callers must not hard-depend)."""
        assert ManifestLoader.load_industries(tmp_path) == {}

    def test_load_industries_explicit_dir(self, tmp_path: Path) -> None:
        """Consumers can point load_industries at a custom config dir."""
        registry = {
            "industries": {
                "custom-vertical": {"description": "Test", "typical_stages": [], "typical_packs": []},
            }
        }
        _write(tmp_path / "industries.yaml", yaml.dump(registry))
        result = ManifestLoader.load_industries(tmp_path)
        assert "custom-vertical" in result
        assert result["custom-vertical"]["description"] == "Test"
