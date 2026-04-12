"""Tests for _shared granular agents."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import pytest
import yaml
from harness.core.manifest_loader import ManifestLoader
from harness.core.composition_engine import CompositionEngine

PROJECT_ROOT = Path(__file__).parent.parent.parent
SHARED = PROJECT_ROOT / "agents" / "_shared"

def _y(p: Path) -> dict:
    return yaml.safe_load(p.read_text()) or {}

AGENTS = ["CodeWriterAgent/v1","TestRunnerAgent/v1","LinterAgent/v1","ReviewerAgent/v1","SpecAnalyzerAgent/v1"]

class TestManifests:
    @pytest.mark.parametrize("name", AGENTS)
    def test_valid(self, name: str) -> None:
        m = _y(SHARED / name / "agent_manifest.yaml")
        assert m["id"] == f"_shared/{name}"
        assert "tools" in m and "input_schema" in m and "output_schema" in m

    @pytest.mark.parametrize("name", AGENTS)
    def test_prompt(self, name: str) -> None:
        p = SHARED / name / "system_prompt.md"
        assert p.exists() and len(p.read_text()) > 100

    @pytest.mark.parametrize("name", AGENTS)
    def test_context_reference(self, name: str) -> None:
        content = (SHARED / name / "system_prompt.md").read_text().lower()
        assert "context" in content or "standard" in content

class TestWorkflows:
    def test_cross_domain_uses_shared(self) -> None:
        wf = _y(PROJECT_ROOT / "workflows/cross_domain/feature_delivery.yaml")
        assert sum(1 for s in wf["steps"] if s["agent"].startswith("_shared/")) >= 4

class TestLoader:
    def test_load_code_writer(self) -> None:
        m, p, _ = ManifestLoader().load_agent(SHARED / "CodeWriterAgent/v1")
        assert m["id"] == "_shared/CodeWriterAgent/v1"
        assert "CodeWriterAgent" in p

