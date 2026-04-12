"""Genesis workflow tests — validates step ordering, dependency injection,
gate evaluation, and output consistency across multiple runs.

Tests use GenesisTestProvider which returns deterministic, schema-correct
outputs for each genesis agent (not generic DryRun stubs).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.core.composition_engine import CompositionEngine
from harness.core.agent_runner import AgentRunner
from harness.core.output_validator import OutputValidator
from harness.tests.genesis_test_provider import GenesisTestProvider, GENESIS_OUTPUTS


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GENESIS_BUILD_WORKFLOW = PROJECT_ROOT / "workflows" / "genesis" / "genesis_build.yaml"
GENESIS_SCAN_WORKFLOW = PROJECT_ROOT / "workflows" / "genesis" / "genesis_scan.yaml"
GENESIS_EVOLVE_WORKFLOW = PROJECT_ROOT / "workflows" / "genesis" / "genesis_evolve.yaml"


# ── Helpers ──────────────────────────────────────────────────────────────

def load_workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def make_engine() -> tuple[CompositionEngine, GenesisTestProvider]:
    provider = GenesisTestProvider()
    runner = AgentRunner(provider=provider)
    validator = OutputValidator()
    engine = CompositionEngine(agent_runner=runner, output_validator=validator)
    return engine, provider


async def run_genesis_pipeline(workflow_path: Path) -> dict:
    """Run a genesis workflow and return the full result."""
    engine, provider = make_engine()
    workflow = load_workflow(workflow_path)
    result = await engine.execute_dag(workflow["steps"])
    result["_provider"] = provider
    return result


# ── Workflow Structure Tests ─────────────────────────────────────────────

class TestGenesisWorkflowStructure:
    """Validate the workflow YAML files are well-formed."""

    def test_build_workflow_exists(self) -> None:
        assert GENESIS_BUILD_WORKFLOW.exists()

    def test_scan_workflow_exists(self) -> None:
        assert GENESIS_SCAN_WORKFLOW.exists()

    def test_evolve_workflow_exists(self) -> None:
        assert GENESIS_EVOLVE_WORKFLOW.exists()

    def test_build_workflow_has_7_steps(self) -> None:
        wf = load_workflow(GENESIS_BUILD_WORKFLOW)
        assert len(wf["steps"]) == 7

    def test_build_workflow_step_names(self) -> None:
        wf = load_workflow(GENESIS_BUILD_WORKFLOW)
        names = [s["name"] for s in wf["steps"]]
        assert names == ["scan", "map", "discover_tools", "engineer_context", "architect", "build", "validate"]

    def test_build_workflow_all_steps_have_agents(self) -> None:
        wf = load_workflow(GENESIS_BUILD_WORKFLOW)
        for step in wf["steps"]:
            assert "agent" in step, f"Step '{step['name']}' missing agent"
            assert step["agent"].startswith("_genesis/"), f"Step '{step['name']}' agent not in _genesis/"

    def test_build_workflow_all_steps_have_gates(self) -> None:
        wf = load_workflow(GENESIS_BUILD_WORKFLOW)
        for step in wf["steps"]:
            assert "gate" in step, f"Step '{step['name']}' missing gate"

    def test_build_workflow_dag_layers(self) -> None:
        """Verify the topological sort produces expected parallel layers."""
        wf = load_workflow(GENESIS_BUILD_WORKFLOW)
        layers = CompositionEngine.topological_sort(wf["steps"])
        # Layer 0: scan (no deps)
        assert "scan" in layers[0]
        # Layer 1: map (depends on scan)
        assert "map" in layers[1]
        # Layer 2: discover_tools + engineer_context (PARALLEL, both depend on map)
        assert set(layers[2]) == {"discover_tools", "engineer_context"}
        # Layer 3: architect (depends on map, discover_tools, engineer_context)
        assert "architect" in layers[3]
        # Layer 4: build
        assert "build" in layers[4]
        # Layer 5: validate
        assert "validate" in layers[5]

    def test_build_workflow_has_feedback_loops(self) -> None:
        wf = load_workflow(GENESIS_BUILD_WORKFLOW)
        assert "feedback_loops" in wf
        assert len(wf["feedback_loops"]) >= 2

    def test_build_workflow_has_budget(self) -> None:
        wf = load_workflow(GENESIS_BUILD_WORKFLOW)
        budget = wf.get("budget", {})
        assert budget.get("max_tokens", 0) > 0
        assert budget.get("max_cost_usd", 0) > 0

    def test_scan_workflow_has_2_steps(self) -> None:
        wf = load_workflow(GENESIS_SCAN_WORKFLOW)
        assert len(wf["steps"]) == 2
        names = [s["name"] for s in wf["steps"]]
        assert names == ["scan", "map"]

    def test_evolve_workflow_has_3_steps(self) -> None:
        wf = load_workflow(GENESIS_EVOLVE_WORKFLOW)
        assert len(wf["steps"]) == 3
        names = [s["name"] for s in wf["steps"]]
        assert names == ["analyze", "apply", "revalidate"]


# ── Genesis Agent Manifest Tests ─────────────────────────────────────────

class TestGenesisAgentManifests:
    """Validate all 8 genesis agent manifests are well-formed."""

    GENESIS_AGENTS = [
        "SourceScannerAgent",
        "KnowledgeMapperAgent",
        "ToolDiscoveryAgent",
        "ContextEngineerAgent",
        "AgentArchitectAgent",
        "AgentBuilderAgent",
        "QualityGateAgent",
        "EvolutionAgent",
    ]

    @pytest.mark.parametrize("agent_name", GENESIS_AGENTS)
    def test_manifest_exists(self, agent_name: str) -> None:
        manifest_path = PROJECT_ROOT / "agents" / "_genesis" / agent_name / "v1" / "agent_manifest.yaml"
        assert manifest_path.exists(), f"Missing manifest: {manifest_path}"

    @pytest.mark.parametrize("agent_name", GENESIS_AGENTS)
    def test_system_prompt_exists(self, agent_name: str) -> None:
        prompt_path = PROJECT_ROOT / "agents" / "_genesis" / agent_name / "v1" / "system_prompt.md"
        assert prompt_path.exists(), f"Missing system_prompt: {prompt_path}"

    @pytest.mark.parametrize("agent_name", GENESIS_AGENTS)
    def test_grading_criteria_exists(self, agent_name: str) -> None:
        criteria_path = PROJECT_ROOT / "agents" / "_genesis" / agent_name / "v1" / "grading_criteria.yaml"
        assert criteria_path.exists(), f"Missing grading_criteria: {criteria_path}"

    @pytest.mark.parametrize("agent_name", GENESIS_AGENTS)
    def test_manifest_required_fields(self, agent_name: str) -> None:
        manifest_path = PROJECT_ROOT / "agents" / "_genesis" / agent_name / "v1" / "agent_manifest.yaml"
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        required = ["id", "domain", "category", "execution_mode", "tools", "permissions"]
        for field in required:
            assert field in data, f"{agent_name} manifest missing field: {field}"

    @pytest.mark.parametrize("agent_name", GENESIS_AGENTS)
    def test_manifest_has_schemas(self, agent_name: str) -> None:
        manifest_path = PROJECT_ROOT / "agents" / "_genesis" / agent_name / "v1" / "agent_manifest.yaml"
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        assert "input_schema" in data, f"{agent_name} missing input_schema"
        assert "output_schema" in data, f"{agent_name} missing output_schema"

    @pytest.mark.parametrize("agent_name", GENESIS_AGENTS)
    def test_manifest_domain_is_genesis(self, agent_name: str) -> None:
        manifest_path = PROJECT_ROOT / "agents" / "_genesis" / agent_name / "v1" / "agent_manifest.yaml"
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        assert data["domain"] == "_genesis"

    @pytest.mark.parametrize("agent_name", GENESIS_AGENTS)
    def test_grading_threshold_at_least_075(self, agent_name: str) -> None:
        criteria_path = PROJECT_ROOT / "agents" / "_genesis" / agent_name / "v1" / "grading_criteria.yaml"
        data = yaml.safe_load(criteria_path.read_text(encoding="utf-8"))
        assert data.get("threshold", 0) >= 0.75, f"{agent_name} grading threshold too low"


# ── Pipeline Execution Tests ────────────────────────────────────────────

class TestGenesisPipelineExecution:
    """Run the genesis pipeline and validate execution behavior."""

    @pytest.mark.asyncio
    async def test_full_pipeline_completes(self) -> None:
        result = await run_genesis_pipeline(GENESIS_BUILD_WORKFLOW)
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_all_7_steps_executed(self) -> None:
        result = await run_genesis_pipeline(GENESIS_BUILD_WORKFLOW)
        expected = {"scan", "map", "discover_tools", "engineer_context", "architect", "build", "validate"}
        assert set(result["step_results"].keys()) == expected

    @pytest.mark.asyncio
    async def test_step_execution_order(self) -> None:
        """Verify steps execute in correct DAG order."""
        result = await run_genesis_pipeline(GENESIS_BUILD_WORKFLOW)
        log = result["execution_log"]
        started = [e["step"] for e in log if e.get("event") == "step_started"]
        # scan must be before map
        assert started.index("scan") < started.index("map")
        # map must be before discover_tools and engineer_context
        assert started.index("map") < started.index("discover_tools")
        assert started.index("map") < started.index("engineer_context")
        # discover_tools and engineer_context before architect
        assert started.index("discover_tools") < started.index("architect")
        assert started.index("engineer_context") < started.index("architect")
        # architect before build
        assert started.index("architect") < started.index("build")
        # build before validate
        assert started.index("build") < started.index("validate")

    @pytest.mark.asyncio
    async def test_dependency_injection_works(self) -> None:
        """Verify that dependency outputs are injected into downstream steps."""
        result = await run_genesis_pipeline(GENESIS_BUILD_WORKFLOW)
        # map depends on scan — map's result should exist
        assert "map" in result["step_results"]
        # architect depends on map, discover_tools, engineer_context
        assert "architect" in result["step_results"]
        # The execution log should show dep injection
        log = result["execution_log"]
        step_started_events = [e for e in log if e.get("event") == "step_started"]
        assert len(step_started_events) == 7

    @pytest.mark.asyncio
    async def test_scan_workflow_completes(self) -> None:
        result = await run_genesis_pipeline(GENESIS_SCAN_WORKFLOW)
        assert result["status"] == "completed"
        assert set(result["step_results"].keys()) == {"scan", "map"}

    @pytest.mark.asyncio
    async def test_evolve_workflow_completes(self) -> None:
        result = await run_genesis_pipeline(GENESIS_EVOLVE_WORKFLOW)
        assert result["status"] == "completed"
        assert set(result["step_results"].keys()) == {"analyze", "apply", "revalidate"}


# ── Test Provider Output Quality ─────────────────────────────────────────

class TestGenesisTestProviderOutputs:
    """Validate that GenesisTestProvider returns schema-correct outputs."""

    def test_source_scanner_output_has_required_fields(self) -> None:
        output = GENESIS_OUTPUTS["SourceScannerAgent"]
        assert "reference_scan" in output
        assert "target_scan" in output
        assert "docs_scan" in output
        assert "scan_quality" in output
        # Reference scan has standards and patterns
        ref = output["reference_scan"]
        assert len(ref["standards_extracted"]) >= 3
        assert len(ref["patterns_found"]) >= 2
        assert "naming" in ref["conventions"]
        # Target scan has tech stack and workflows
        tgt = output["target_scan"]
        assert tgt["tech_stack"]["language"] != ""
        assert len(tgt["workflow_processes"]) >= 2
        # Docs scan has inventory
        assert len(output["docs_scan"]["inventory"]) >= 1

    def test_knowledge_mapper_output_has_coverage(self) -> None:
        output = GENESIS_OUTPUTS["KnowledgeMapperAgent"]
        assert "knowledge_map" in output
        assert "coverage" in output
        assert "gaps" in output
        assert output["coverage"]["overall"] > 0

    def test_tool_discovery_output_has_tools(self) -> None:
        output = GENESIS_OUTPUTS["ToolDiscoveryAgent"]
        assert "tools_discovered" in output
        assert "mcp_config" in output
        assert "tool_packs" in output
        assert len(output["tools_discovered"]) >= 3

    def test_context_engineer_output_has_documents(self) -> None:
        output = GENESIS_OUTPUTS["ContextEngineerAgent"]
        assert "documents" in output
        assert "quality_scores" in output
        docs = output["documents"]
        assert len(docs["standards_md"]) > 100
        assert len(docs["reference_docs"]) >= 1

    def test_agent_architect_output_has_roster(self) -> None:
        output = GENESIS_OUTPUTS["AgentArchitectAgent"]
        assert "agent_roster" in output
        assert "workflow_design" in output
        assert "design_quality" in output
        assert len(output["agent_roster"]) >= 4
        assert output["design_quality"]["dag_valid"] is True

    def test_agent_builder_output_has_files(self) -> None:
        output = GENESIS_OUTPUTS["AgentBuilderAgent"]
        assert "files_created" in output
        assert "build_quality" in output
        assert output["build_quality"]["completion_pct"] == 100.0
        assert len(output["files_created"]) >= 10

    def test_quality_gate_output_has_score(self) -> None:
        output = GENESIS_OUTPUTS["QualityGateAgent"]
        assert "validation_passed" in output
        assert "overall_score" in output
        assert "issues" in output
        assert "targeted_feedback" in output
        assert output["overall_score"] > 0

    def test_evolution_output_has_health(self) -> None:
        output = GENESIS_OUTPUTS["EvolutionAgent"]
        assert "domain_health" in output
        assert "improvements" in output
        assert output["domain_health"]["overall_score"] > 0


# ── Consistency Tests (Multiple Runs) ───────────────────────────────────

class TestGenesisConsistency:
    """Run the pipeline multiple times, verify outputs are identical."""

    @pytest.mark.asyncio
    async def test_5_runs_produce_identical_results(self) -> None:
        """Run the full genesis pipeline 5 times. All outputs must be identical."""
        results = []
        for _ in range(5):
            result = await run_genesis_pipeline(GENESIS_BUILD_WORKFLOW)
            results.append(result)

        # All runs must complete
        for i, r in enumerate(results):
            assert r["status"] == "completed", f"Run {i+1} failed: {r.get('error', 'unknown')}"

        # Same steps executed in each run
        step_sets = [set(r["step_results"].keys()) for r in results]
        assert all(s == step_sets[0] for s in step_sets), "Step sets differ across runs"

        # Same step outputs in each run (deterministic provider)
        for step_name in results[0]["step_results"]:
            outputs = [r["step_results"][step_name].get("output", "") for r in results]
            for i in range(1, len(outputs)):
                assert outputs[i] == outputs[0], (
                    f"Run {i+1} output differs from run 1 for step '{step_name}'"
                )

    @pytest.mark.asyncio
    async def test_5_runs_same_execution_order(self) -> None:
        """Verify execution order is consistent across 5 runs."""
        orders = []
        for _ in range(5):
            result = await run_genesis_pipeline(GENESIS_BUILD_WORKFLOW)
            log = result["execution_log"]
            order = [e["step"] for e in log if e.get("event") == "step_started"]
            orders.append(order)

        for i in range(1, len(orders)):
            assert orders[i] == orders[0], (
                f"Run {i+1} execution order differs:\n  Run 1: {orders[0]}\n  Run {i+1}: {orders[i]}"
            )

    @pytest.mark.asyncio
    async def test_5_runs_same_step_count(self) -> None:
        """Verify the same number of steps execute each time."""
        counts = []
        for _ in range(5):
            result = await run_genesis_pipeline(GENESIS_BUILD_WORKFLOW)
            counts.append(len(result["step_results"]))

        assert all(c == counts[0] for c in counts), f"Step counts vary: {counts}"

    @pytest.mark.asyncio
    async def test_scan_workflow_5_runs_consistent(self) -> None:
        """Scan workflow must also be consistent across 5 runs."""
        results = []
        for _ in range(5):
            result = await run_genesis_pipeline(GENESIS_SCAN_WORKFLOW)
            results.append(result)

        for i in range(1, len(results)):
            assert results[i]["status"] == results[0]["status"]
            r0_steps = set(results[0]["step_results"].keys())
            ri_steps = set(results[i]["step_results"].keys())
            assert ri_steps == r0_steps


# ── Config Tests ─────────────────────────────────────────────────────────

class TestGenesisConfig:
    """Validate genesis configuration files."""

    def test_genesis_rules_exists(self) -> None:
        rules_path = PROJECT_ROOT / "config" / "genesis_rules.yaml"
        assert rules_path.exists()

    def test_genesis_rules_has_quality_thresholds(self) -> None:
        rules_path = PROJECT_ROOT / "config" / "genesis_rules.yaml"
        data = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
        genesis = data.get("genesis", {})
        quality = genesis.get("quality", {})
        assert quality.get("grading_pass_threshold", 0) >= 0.85

    def test_genesis_rules_self_modification_safety(self) -> None:
        rules_path = PROJECT_ROOT / "config" / "genesis_rules.yaml"
        data = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
        genesis = data.get("genesis", {})
        self_mod = genesis.get("self_modification", {})
        assert self_mod.get("requires_golden_test_pass") is True
        assert self_mod.get("cannot_modify_golden_tests") is True
        assert self_mod.get("cannot_reduce_thresholds") is True

    def test_golden_test_fixture_exists(self) -> None:
        fixture = PROJECT_ROOT / "tests" / "golden" / "test_it_backend" / "golden_test.yaml"
        assert fixture.exists()

    def test_golden_test_has_assertions(self) -> None:
        fixture = PROJECT_ROOT / "tests" / "golden" / "test_it_backend" / "golden_test.yaml"
        data = yaml.safe_load(fixture.read_text(encoding="utf-8"))
        assert "assertions" in data
        assert len(data["assertions"]) >= 3
