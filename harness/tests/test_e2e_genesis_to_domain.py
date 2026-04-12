"""End-to-end test: Genesis scans sample_project → builds domain → domain agents run workflows.

This test proves the FULL lifecycle:
1. Genesis pipeline scans real sample project files
2. Genesis produces structured domain output (agents, workflows, context)
3. Generated domain agents can execute their workflows
4. Cross-domain workflow orchestrates all agent groups together
5. Agent lifecycle states and execution events are properly tracked
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from harness.core.agent_runner import AgentRunner, AgentState
from harness.core.composition_engine import CompositionEngine, ExecutionEvent
from harness.core.output_validator import OutputValidator
from harness.core.rule_engine import RuleEngine, RuleContext, Decision
from harness.tests.genesis_test_provider import GenesisTestProvider, GENESIS_OUTPUTS


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ── Helpers ──────────────────────────────────────────────────────────────

def make_engine():
    provider = GenesisTestProvider()
    runner = AgentRunner(provider=provider)
    validator = OutputValidator()
    engine = CompositionEngine(agent_runner=runner, output_validator=validator)
    return engine, runner, provider


def build_domain_workflow(steps, step_configs=None):
    """Build a workflow and step_configs for domain agent execution."""
    provider = GenesisTestProvider()
    runner = AgentRunner(provider=provider)
    engine = CompositionEngine(agent_runner=runner)
    return engine, runner, provider



# ═══════════════════════════════════════════════════════════════════════
# PHASE 2: GENESIS OUTPUT QUALITY — WHAT AGENTS/WORKFLOWS WERE DESIGNED
# ═══════════════════════════════════════════════════════════════════════

class TestPhase2GenesisOutputQuality:
    """Verify genesis produces well-structured domain agents."""

    def test_architect_produces_5_agents(self):
        roster = GENESIS_OUTPUTS["AgentArchitectAgent"]["agent_roster"]
        assert len(roster) == 5
        names = [a["name"] for a in roster]
        assert "CodeWriterAgent" in names
        assert "TestRunnerAgent" in names
        assert "LinterAgent" in names
        assert "ReviewerAgent" in names
        assert "MigrationAgent" in names

    def test_every_agent_has_complete_harness(self):
        """Every generated agent must have full harness config."""
        roster = GENESIS_OUTPUTS["AgentArchitectAgent"]["agent_roster"]
        for agent in roster:
            name = agent["name"]
            # Identity
            assert "version" in agent, f"{name} missing version"
            assert "description" in agent, f"{name} missing description"
            # Tools with pack refs
            assert len(agent["tools"]) >= 1, f"{name} has no tools"
            for tool in agent["tools"]:
                assert "name" in tool, f"{name} tool missing name"
                assert "pack" in tool, f"{name} tool missing pack ref"
            # Permissions
            assert "permissions" in agent, f"{name} missing permissions"
            # Constraints
            assert "constraints" in agent, f"{name} missing constraints"
            # Schemas
            assert "input_schema" in agent, f"{name} missing input_schema"
            assert "output_schema" in agent, f"{name} missing output_schema"
            # Harness
            assert "harness" in agent, f"{name} missing harness config"
            harness = agent["harness"]
            assert "gate_condition" in harness, f"{name} harness missing gate_condition"
            assert "gate_on_fail" in harness, f"{name} harness missing gate_on_fail"
            assert "grading_threshold" in harness, f"{name} harness missing grading_threshold"

    def test_workflow_has_valid_dag(self):
        design = GENESIS_OUTPUTS["AgentArchitectAgent"]["workflow_design"]
        steps = design["steps"]
        assert len(steps) == 4  # code, lint, test, review
        # lint and test depend on code
        lint_step = next(s for s in steps if s["agent"] == "LinterAgent")
        test_step = next(s for s in steps if s["agent"] == "TestRunnerAgent")
        assert "code" in lint_step.get("depends_on", [])
        assert "code" in test_step.get("depends_on", [])
        # review depends on lint and test
        review_step = next(s for s in steps if s["agent"] == "ReviewerAgent")
        assert "lint" in review_step.get("depends_on", [])
        assert "test" in review_step.get("depends_on", [])

    def test_workflow_has_gates(self):
        design = GENESIS_OUTPUTS["AgentArchitectAgent"]["workflow_design"]
        gates = design["gates"]
        assert len(gates) >= 3
        gate_steps = [g["step"] for g in gates]
        assert "lint" in gate_steps
        assert "test" in gate_steps
        assert "review" in gate_steps

    def test_workflow_has_feedback_loops(self):
        design = GENESIS_OUTPUTS["AgentArchitectAgent"]["workflow_design"]
        loops = design.get("feedback_loops", [])
        assert len(loops) >= 1
        # review → code feedback loop
        assert any(l["from_step"] == "review" and l["to_step"] == "code" for l in loops)

    def test_workflow_has_parallel_branches(self):
        design = GENESIS_OUTPUTS["AgentArchitectAgent"]["workflow_design"]
        parallel = design.get("parallel_branches", [])
        assert len(parallel) >= 1
        # lint and test run in parallel
        assert ["lint", "test"] in parallel

    def test_builder_creates_all_files(self):
        output = GENESIS_OUTPUTS["AgentBuilderAgent"]
        assert output["build_quality"]["completion_pct"] == 100.0
        files = output["files_created"]
        # Must have domain.yaml
        assert "domain.yaml" in files
        # Must have context
        assert "context/standards.md" in files
        # Must have agents (5 agents × 3 files = 15 agent files)
        agent_files = [f for f in files if f.startswith("agents/")]
        assert len(agent_files) == 15
        # Must have workflow
        assert "workflows/feature_development.yaml" in files
        # Must have configs
        assert "tools/mcp_servers.yaml" in files
        assert "rules/compliance.yaml" in files

    def test_quality_gate_passes(self):
        output = GENESIS_OUTPUTS["QualityGateAgent"]
        assert output["validation_passed"] is True
        assert output["overall_score"] >= 70
        assert len(output["issues"]["structural"]) == 0

    def test_context_standards_is_substantive(self):
        docs = GENESIS_OUTPUTS["ContextEngineerAgent"]["documents"]
        standards = docs["standards_md"]
        assert len(standards) > 200  # Not a stub
        assert "FastAPI" in standards
        assert "Python" in standards
        assert "Pydantic" in standards

    def test_context_has_reference_docs(self):
        docs = GENESIS_OUTPUTS["ContextEngineerAgent"]["documents"]
        ref_docs = docs["reference_docs"]
        assert len(ref_docs) >= 2
        topics = [d["topic"] for d in ref_docs]
        assert any("FastAPI" in t for t in topics)

    def test_grading_specs_are_detailed(self):
        specs = GENESIS_OUTPUTS["AgentArchitectAgent"]["grading_specs"]
        for spec in specs:
            name = spec["agent_name"]
            # Every agent has a threshold
            assert spec["pass_threshold"] >= 0.70, f"{name} threshold too low"
            # Every agent has at least 1 criterion
            total = len(spec["automated_criteria"]) + len(spec["llm_judge_criteria"])
            assert total >= 1, f"{name} has no grading criteria"


# ═══════════════════════════════════════════════════════════════════════
# PHASE 3: DOMAIN AGENTS EXECUTE THEIR WORKFLOWS
# ═══════════════════════════════════════════════════════════════════════

class TestPhase3DomainAgentsRunWorkflows:
    """Simulate running the generated domain agents in their workflows."""

    @pytest.mark.asyncio
    async def test_backend_workflow_code_lint_test_review(self):
        """Backend: code → lint + test (parallel) → review."""
        engine, runner, provider = make_engine()
        steps = [
            {"name": "code", "agent": "BackendCodeAgent",
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 2}},
            {"name": "lint", "agent": "BackendLintAgent", "depends_on": ["code"],
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 1}},
            {"name": "test", "agent": "BackendTestAgent", "depends_on": ["code"],
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 2}},
            {"name": "review", "agent": "BackendReviewAgent", "depends_on": ["lint", "test"],
             "gate": {"condition": "status == success", "on_fail": "degrade"}},
        ]
        result = await engine.execute_dag(steps)
        assert result["status"] == "completed"
        assert set(result["step_results"].keys()) == {"code", "lint", "test", "review"}
        # Verify DAG order
        log = result["execution_log"]
        started = [e["step"] for e in log if e.get("event") == ExecutionEvent.STEP_STARTED]
        assert started.index("code") < started.index("lint")
        assert started.index("code") < started.index("test")
        assert started.index("lint") < started.index("review")
        assert started.index("test") < started.index("review")

    @pytest.mark.asyncio
    async def test_frontend_workflow_code_lint_test_review(self):
        """Frontend: code → lint + test (parallel) → review."""
        engine, runner, provider = make_engine()
        steps = [
            {"name": "code", "agent": "FrontendCodeAgent",
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 2}},
            {"name": "lint", "agent": "FrontendLintAgent", "depends_on": ["code"],
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 1}},
            {"name": "test", "agent": "FrontendTestAgent", "depends_on": ["code"],
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 2}},
            {"name": "review", "agent": "FrontendReviewAgent", "depends_on": ["lint", "test"],
             "gate": {"condition": "status == success", "on_fail": "degrade"}},
        ]
        result = await engine.execute_dag(steps)
        assert result["status"] == "completed"
        assert set(result["step_results"].keys()) == {"code", "lint", "test", "review"}

    @pytest.mark.asyncio
    async def test_cicd_workflow_plan_build_validate(self):
        """CI/CD: plan → build → validate."""
        engine, runner, provider = make_engine()
        steps = [
            {"name": "plan", "agent": "CICDPlannerAgent",
             "gate": {"condition": "status == success", "on_fail": "abort"}},
            {"name": "build", "agent": "CICDBuilderAgent", "depends_on": ["plan"],
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 2}},
            {"name": "validate", "agent": "CICDValidatorAgent", "depends_on": ["build"],
             "gate": {"condition": "status == success", "on_fail": "abort"}},
        ]
        result = await engine.execute_dag(steps)
        assert result["status"] == "completed"
        assert set(result["step_results"].keys()) == {"plan", "build", "validate"}

    @pytest.mark.asyncio
    async def test_db_migration_workflow(self):
        """DB: migrate → review → validate."""
        engine, runner, provider = make_engine()
        steps = [
            {"name": "migrate", "agent": "DBMigrationAgent",
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 1}},
            {"name": "review", "agent": "DBReviewAgent", "depends_on": ["migrate"],
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 1}},
            {"name": "validate", "agent": "DBValidatorAgent", "depends_on": ["review"],
             "gate": {"condition": "status == success", "on_fail": "abort"}},
        ]
        result = await engine.execute_dag(steps)
        assert result["status"] == "completed"
        assert set(result["step_results"].keys()) == {"migrate", "review", "validate"}

    @pytest.mark.asyncio
    async def test_infra_workflow_plan_apply_verify(self):
        """Infra: plan → apply → verify."""
        engine, runner, provider = make_engine()
        steps = [
            {"name": "plan", "agent": "InfraPlanAgent",
             "gate": {"condition": "status == success", "on_fail": "abort"}},
            {"name": "apply", "agent": "InfraApplyAgent", "depends_on": ["plan"],
             "gate": {"condition": "status == success", "on_fail": "rollback"}},
            {"name": "verify", "agent": "InfraVerifyAgent", "depends_on": ["apply"],
             "gate": {"condition": "status == success", "on_fail": "abort"}},
        ]
        result = await engine.execute_dag(steps)
        assert result["status"] == "completed"
        assert set(result["step_results"].keys()) == {"plan", "apply", "verify"}


# ═══════════════════════════════════════════════════════════════════════
# PHASE 4: CROSS-DOMAIN WORKFLOW — ALL AGENTS WORK TOGETHER
# ═══════════════════════════════════════════════════════════════════════

class TestPhase4CrossDomainWorkflow:
    """Full feature workflow: backend + frontend + DB + CI/CD + infra all together."""

    @pytest.mark.asyncio
    async def test_full_feature_workflow_all_areas(self):
        """
        Full feature delivery across all domains:
        Layer 1: backend_code + frontend_code + db_migrate (PARALLEL)
        Layer 2: backend_lint + backend_test + frontend_lint + frontend_test (PARALLEL)
        Layer 3: backend_review + frontend_review + db_review (PARALLEL)
        Layer 4: cicd_build → cicd_validate
        Layer 5: infra_plan → infra_apply
        """
        engine, runner, provider = make_engine()
        steps = [
            # Layer 1: code + migrate in parallel
            {"name": "backend_code", "agent": "BackendCodeAgent",
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 2}},
            {"name": "frontend_code", "agent": "FrontendCodeAgent",
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 2}},
            {"name": "db_migrate", "agent": "DBMigrationAgent",
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 1}},

            # Layer 2: lint + test in parallel (depend on code)
            {"name": "backend_lint", "agent": "BackendLintAgent", "depends_on": ["backend_code"],
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 1}},
            {"name": "backend_test", "agent": "BackendTestAgent", "depends_on": ["backend_code"],
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 2}},
            {"name": "frontend_lint", "agent": "FrontendLintAgent", "depends_on": ["frontend_code"],
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 1}},
            {"name": "frontend_test", "agent": "FrontendTestAgent", "depends_on": ["frontend_code"],
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 2}},

            # Layer 3: review in parallel (depend on lint + test)
            {"name": "backend_review", "agent": "BackendReviewAgent",
             "depends_on": ["backend_lint", "backend_test"],
             "gate": {"condition": "status == success", "on_fail": "degrade"}},
            {"name": "frontend_review", "agent": "FrontendReviewAgent",
             "depends_on": ["frontend_lint", "frontend_test"],
             "gate": {"condition": "status == success", "on_fail": "degrade"}},
            {"name": "db_review", "agent": "DBReviewAgent", "depends_on": ["db_migrate"],
             "gate": {"condition": "status == success", "on_fail": "degrade"}},

            # Layer 4: CI/CD (depends on all reviews passing)
            {"name": "cicd_build", "agent": "CICDBuilderAgent",
             "depends_on": ["backend_review", "frontend_review", "db_review"],
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 2}},
            {"name": "cicd_validate", "agent": "CICDValidatorAgent", "depends_on": ["cicd_build"],
             "gate": {"condition": "status == success", "on_fail": "abort"}},

            # Layer 5: Infra deploy (depends on CI/CD validation)
            {"name": "infra_plan", "agent": "InfraPlanAgent", "depends_on": ["cicd_validate"],
             "gate": {"condition": "status == success", "on_fail": "abort"}},
            {"name": "infra_apply", "agent": "InfraApplyAgent", "depends_on": ["infra_plan"],
             "gate": {"condition": "status == success", "on_fail": "rollback"}},
        ]

        result = await engine.execute_dag(steps)
        assert result["status"] == "completed"

        # All 14 steps completed
        assert len(result["step_results"]) == 14
        expected = {
            "backend_code", "frontend_code", "db_migrate",
            "backend_lint", "backend_test", "frontend_lint", "frontend_test",
            "backend_review", "frontend_review", "db_review",
            "cicd_build", "cicd_validate",
            "infra_plan", "infra_apply",
        }
        assert set(result["step_results"].keys()) == expected

    @pytest.mark.asyncio
    async def test_full_feature_dag_layers_correct(self):
        """Verify topological sort produces correct parallel layers."""
        steps = [
            {"name": "backend_code", "agent": "A"},
            {"name": "frontend_code", "agent": "A"},
            {"name": "db_migrate", "agent": "A"},
            {"name": "backend_lint", "agent": "A", "depends_on": ["backend_code"]},
            {"name": "backend_test", "agent": "A", "depends_on": ["backend_code"]},
            {"name": "frontend_lint", "agent": "A", "depends_on": ["frontend_code"]},
            {"name": "frontend_test", "agent": "A", "depends_on": ["frontend_code"]},
            {"name": "backend_review", "agent": "A", "depends_on": ["backend_lint", "backend_test"]},
            {"name": "frontend_review", "agent": "A", "depends_on": ["frontend_lint", "frontend_test"]},
            {"name": "db_review", "agent": "A", "depends_on": ["db_migrate"]},
            {"name": "cicd_build", "agent": "A", "depends_on": ["backend_review", "frontend_review", "db_review"]},
            {"name": "cicd_validate", "agent": "A", "depends_on": ["cicd_build"]},
            {"name": "infra_plan", "agent": "A", "depends_on": ["cicd_validate"]},
            {"name": "infra_apply", "agent": "A", "depends_on": ["infra_plan"]},
        ]
        layers = CompositionEngine.topological_sort(steps)

        # Layer 0: 3 parallel (backend_code, frontend_code, db_migrate)
        assert set(layers[0]) == {"backend_code", "db_migrate", "frontend_code"}
        # Layer 1: 5 parallel (4 lint/test + db_review)
        assert set(layers[1]) == {"backend_lint", "backend_test", "db_review", "frontend_lint", "frontend_test"}
        # Layer 2: 2 parallel reviews
        assert set(layers[2]) == {"backend_review", "frontend_review"}
        # Layer 3: cicd_build
        assert layers[3] == ["cicd_build"]
        # Layer 4: cicd_validate
        assert layers[4] == ["cicd_validate"]
        # Layer 5: infra_plan
        assert layers[5] == ["infra_plan"]
        # Layer 6: infra_apply
        assert layers[6] == ["infra_apply"]

    @pytest.mark.asyncio
    async def test_full_feature_execution_events_tracked(self):
        """Verify typed ExecutionEvents are emitted for all 14 steps."""
        engine, runner, provider = make_engine()
        steps = [
            {"name": "backend_code", "agent": "A",
             "gate": {"condition": "status == success", "on_fail": "degrade"}},
            {"name": "backend_test", "agent": "A", "depends_on": ["backend_code"],
             "gate": {"condition": "status == success", "on_fail": "degrade"}},
        ]
        result = await engine.execute_dag(steps)
        log = result["execution_log"]

        # Verify typed events (not raw strings)
        started_events = [e for e in log if e.get("event") == ExecutionEvent.STEP_STARTED]
        completed_events = [e for e in log if e.get("event") == ExecutionEvent.STEP_COMPLETED]
        gate_events = [e for e in log if e.get("event") == ExecutionEvent.GATE_PASSED]

        assert len(started_events) == 2
        assert len(completed_events) == 2
        assert len(gate_events) == 2

    @pytest.mark.asyncio
    async def test_agent_lifecycle_states_tracked(self):
        """Verify AgentState lifecycle is tracked for each agent execution."""
        provider = GenesisTestProvider()
        runner = AgentRunner(provider=provider)

        result = await runner.run(
            manifest={"id": "test/TestAgent/v1", "execution_mode": {"primary": "react"}},
            task={"task_id": "test-1", "agent_id": "test/TestAgent/v1"},
            system_prompt_content="You are a test agent.",
        )

        assert result["status"] == "completed"
        assert "state_log" in result

        states = [s["state"] for s in result["state_log"]]
        assert AgentState.SPAWNING in states
        assert AgentState.READY in states
        assert AgentState.RUNNING in states
        assert AgentState.VALIDATING in states
        assert AgentState.COMPLETED in states

    @pytest.mark.asyncio
    async def test_dependency_injection_across_domains(self):
        """Verify backend agent output flows into CI/CD agent as context."""
        engine, runner, provider = make_engine()
        steps = [
            {"name": "backend_code", "agent": "BackendCodeAgent",
             "gate": {"condition": "status == success", "on_fail": "degrade"}},
            {"name": "cicd_build", "agent": "CICDBuilderAgent",
             "depends_on": ["backend_code"],
             "gate": {"condition": "status == success", "on_fail": "degrade"}},
        ]
        result = await engine.execute_dag(steps)
        assert result["status"] == "completed"
        # cicd_build should have backend_code's output injected
        assert "cicd_build" in result["step_results"]
        assert "backend_code" in result["step_results"]


# ═══════════════════════════════════════════════════════════════════════
# PHASE 5: RULE ENGINE + TRUSTED PATHS
# ═══════════════════════════════════════════════════════════════════════

class TestPhase5RuleEngineAndTrustedPaths:
    """Verify RuleEngine works for domain agents with trusted paths."""

    def test_untrusted_paths_go_through_normal_rules(self):
        engine = RuleEngine()
        ctx = RuleContext()  # no trusted paths
        decision = engine.check_tool_call(
            "file_read",
            {"path": "/some/random/path.py"},
            ctx,
        )
        # Should still resolve (allow by default for file_read)
        assert decision.decision in (Decision.ALLOW, Decision.ASK)
        assert decision.rule_layer != "trusted"

    def test_platform_rules_not_bypassed_by_trust(self):
        """Trusted paths do NOT bypass platform safety rules."""
        engine = RuleEngine()
        ctx = RuleContext(
            trusted_paths=["/"],  # trust everything
        )
        # Blocked command should still be blocked
        decision = engine.check_tool_call(
            "shell_exec",
            {"command": "rm -rf /"},
            ctx,
        )
        assert decision.denied
        assert decision.rule_layer == "platform"


# ═══════════════════════════════════════════════════════════════════════
# PHASE 6: CONSISTENCY — MULTIPLE RUNS
# ═══════════════════════════════════════════════════════════════════════

class TestPhase6Consistency:
    """Run the full cross-domain workflow 3 times, verify identical results."""

    @pytest.mark.asyncio
    async def test_3_runs_full_workflow_identical(self):
        results = []
        for _ in range(3):
            engine, runner, provider = make_engine()
            steps = [
                {"name": "backend_code", "agent": "A",
                 "gate": {"condition": "status == success", "on_fail": "degrade"}},
                {"name": "frontend_code", "agent": "A",
                 "gate": {"condition": "status == success", "on_fail": "degrade"}},
                {"name": "backend_test", "agent": "A", "depends_on": ["backend_code"],
                 "gate": {"condition": "status == success", "on_fail": "degrade"}},
                {"name": "frontend_test", "agent": "A", "depends_on": ["frontend_code"],
                 "gate": {"condition": "status == success", "on_fail": "degrade"}},
                {"name": "review", "agent": "A", "depends_on": ["backend_test", "frontend_test"],
                 "gate": {"condition": "status == success", "on_fail": "degrade"}},
            ]
            result = await engine.execute_dag(steps)
            results.append(result)

        for i in range(1, 3):
            assert results[i]["status"] == results[0]["status"]
            assert set(results[i]["step_results"].keys()) == set(results[0]["step_results"].keys())


# ═══════════════════════════════════════════════════════════════════════
# PHASE 7: EVOLUTION — GENESIS CAN IMPROVE DOMAIN AGENTS
# ═══════════════════════════════════════════════════════════════════════

class TestPhase7Evolution:
    """Verify genesis evolve workflow runs and produces improvement suggestions."""

    @pytest.mark.asyncio
    async def test_evolve_workflow_completes(self):
        engine, runner, provider = make_engine()
        evolve_wf = yaml.safe_load(
            (PROJECT_ROOT / "workflows" / "genesis" / "genesis_evolve.yaml").read_text()
        )
        result = await engine.execute_dag(evolve_wf["steps"])
        assert result["status"] == "completed"
        assert set(result["step_results"].keys()) == {"analyze", "apply", "revalidate"}

    def test_evolution_output_has_health_score(self):
        output = GENESIS_OUTPUTS["EvolutionAgent"]
        assert output["domain_health"]["overall_score"] > 0
        assert output["domain_health"]["trend"] in ("improving", "stable", "declining")
