"""Tests for HouseStyleAgent and the house_style_sync maintenance workflow.

Covers Gap 6 from the plan: cross-domain style arbitration. The agent reads
every registered domain's standards.md and emits a unified org_standards.md
plus per-domain divergence reports — without any new workspace.yaml config.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.core.expr import evaluate
from harness.core.manifest_loader import ManifestLoader
from harness.tests.genesis_test_provider import GENESIS_OUTPUTS, GenesisTestProvider


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AGENT_DIR = PROJECT_ROOT / "agents" / "_genesis" / "HouseStyleAgent" / "v1"
SYNC_WF = PROJECT_ROOT / "workflows" / "maintenance" / "house_style_sync.yaml"


class TestHouseStyleBundle:
    def test_bundle_files_present(self) -> None:
        assert (AGENT_DIR / "agent_manifest.yaml").exists()
        assert (AGENT_DIR / "system_prompt.md").exists()
        assert (AGENT_DIR / "grading_criteria.yaml").exists()

    def test_grading_threshold_at_least_075(self) -> None:
        data = yaml.safe_load((AGENT_DIR / "grading_criteria.yaml").read_text())
        assert data.get("threshold", 0) >= 0.75

    def test_output_schema_has_org_standards(self) -> None:
        manifest = yaml.safe_load((AGENT_DIR / "agent_manifest.yaml").read_text())
        required = manifest["output_schema"]["required"]
        assert "org_standards_path" in required
        assert "divergences" in required
        assert "sync_summary" in required


class TestHouseStyleWorkflow:
    def test_workflow_file_present(self) -> None:
        assert SYNC_WF.exists()

    def test_workflow_single_sync_step(self) -> None:
        wf = yaml.safe_load(SYNC_WF.read_text())
        assert len(wf["steps"]) == 1
        assert wf["steps"][0]["name"] == "sync"
        assert "HouseStyleAgent" in wf["steps"][0]["agent"]

    def test_sync_gate_passes_on_fixture(self) -> None:
        wf = yaml.safe_load(SYNC_WF.read_text())
        cond = wf["steps"][0]["gate"]["condition"]
        ctx = {
            "status": "success",
            "output": GENESIS_OUTPUTS["HouseStyleAgent"],
        }
        assert evaluate(cond, ctx) is True

    def test_sync_gate_blocks_on_missing_output(self) -> None:
        wf = yaml.safe_load(SYNC_WF.read_text())
        cond = wf["steps"][0]["gate"]["condition"]
        ctx = {
            "status": "success",
            "output": {"org_standards_path": None, "sync_summary": {"domains_scanned": 0}},
        }
        assert evaluate(cond, ctx) is False


class TestHouseStyleRuns:
    @pytest.mark.asyncio
    async def test_sync_workflow_completes_via_provider(self) -> None:
        loader = ManifestLoader()
        provider = GenesisTestProvider()
        engine, workflow, cfgs = loader.boot_engine(
            SYNC_WF,
            domain_root=PROJECT_ROOT,
            provider=provider,
            task_input={
                "domains": [
                    {"name": "backend-fastapi", "standards_path": "domain-backend/context/standards.md"},
                ],
            },
        )
        result = await engine.execute_dag(workflow["steps"], cfgs)
        assert result["status"] == "completed"
        sync = next(iter(result["step_results"].values()))
        out = sync.get("output")
        assert isinstance(out, dict)
        assert "org_standards_path" in out
        assert "sync_summary" in out
