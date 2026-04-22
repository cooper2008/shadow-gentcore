"""Tests for DomainPlannerAgent and genesis_org_plan workflow.

Covers the zero-config contract from the Genesis Complexity Upgrade plan:
the planner infers natural domain boundaries without asking the user to
hand-author priority/version/split metadata.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.core.manifest_loader import ManifestLoader
from harness.tests.genesis_test_provider import GenesisTestProvider


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AGENT_DIR = PROJECT_ROOT / "agents" / "_genesis" / "DomainPlannerAgent" / "v1"
ORG_PLAN_WORKFLOW = PROJECT_ROOT / "workflows" / "genesis" / "genesis_org_plan.yaml"


class TestDomainPlannerManifest:
    def test_manifest_exists(self) -> None:
        assert (AGENT_DIR / "agent_manifest.yaml").exists()
        assert (AGENT_DIR / "system_prompt.md").exists()
        assert (AGENT_DIR / "grading_criteria.yaml").exists()

    def test_manifest_shape(self) -> None:
        data = yaml.safe_load((AGENT_DIR / "agent_manifest.yaml").read_text())
        assert data["id"] == "_genesis/DomainPlannerAgent/v1"
        assert data["domain"] == "_genesis"
        assert "input_schema" in data
        assert "output_schema" in data
        required = data["output_schema"].get("required", [])
        assert "decision" in required
        assert "domain_plan" in required

    def test_grading_threshold(self) -> None:
        data = yaml.safe_load((AGENT_DIR / "grading_criteria.yaml").read_text())
        assert data.get("threshold", 0) >= 0.75

    def test_declares_no_new_config_keys(self) -> None:
        """Plan principle: no new required workspace.yaml keys."""
        sp = (AGENT_DIR / "system_prompt.md").read_text()
        assert "priority" not in sp.lower().split("workspace.yaml")[0][-400:] or "no new" in sp.lower()


class TestGenesisOrgPlanWorkflow:
    def test_workflow_exists(self) -> None:
        assert ORG_PLAN_WORKFLOW.exists()

    def test_workflow_references_planner(self) -> None:
        wf = yaml.safe_load(ORG_PLAN_WORKFLOW.read_text())
        agents = {s["agent"] for s in wf["steps"]}
        assert any("DomainPlannerAgent" in a for a in agents)


class TestDomainPlannerRuns:
    @pytest.mark.asyncio
    async def test_planner_runs_via_provider(self) -> None:
        """End-to-end: planner executes through boot_engine + provider."""
        loader = ManifestLoader()
        provider = GenesisTestProvider()
        engine, workflow, cfgs = loader.boot_engine(
            ORG_PLAN_WORKFLOW,
            domain_root=PROJECT_ROOT,
            provider=provider,
            task_input={
                "industry": "fintech",
                "team_name": "big-org",
                "team_config": {
                    "reference": [
                        {"path": str(PROJECT_ROOT / "sample_project" / "backend")},
                        {"path": str(PROJECT_ROOT / "sample_project" / "frontend")},
                    ],
                    "target": [{"path": str(PROJECT_ROOT / "sample_project" / "backend")}],
                    "docs": [{"path": str(PROJECT_ROOT / "sample_project" / "docs"), "type": "documents"}],
                    "industry": "fintech",
                },
            },
        )
        result = await engine.execute_dag(workflow["steps"], cfgs)
        assert result["status"] == "completed"
        plan_step = next(iter(result["step_results"].values()))
        output = plan_step.get("output")
        assert isinstance(output, dict)
        assert output.get("decision") in {"single-domain", "auto-split", "ask-human"}
        assert isinstance(output.get("domain_plan"), list)

    @pytest.mark.asyncio
    async def test_ask_human_escape_hatch_valid(self) -> None:
        """Decision enum must include ask-human so low-confidence splits surface."""
        manifest = yaml.safe_load((AGENT_DIR / "agent_manifest.yaml").read_text())
        decision_schema = manifest["output_schema"]["properties"]["decision"]
        enum_values = decision_schema.get("enum", [])
        assert "ask-human" in enum_values
        assert "single-domain" in enum_values
        assert "auto-split" in enum_values
