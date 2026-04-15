"""Pytest e2e smoke tests — full user journey validation with zero API tokens.

All tests use tmp_path for full isolation (zero repo pollution).
Uses SmokeTestProvider (schema-aware embedded provider, zero API tokens).

Run:
    pytest harness/tests/smoke/test_smoke_journey.py -v
    pytest harness/tests/smoke/ -v  # full suite including unit tests
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.tests.smoke.preflight import PreflightCheck, CheckLevel
from harness.tests.smoke.smoke_runner import SmokeRunner, _scaffold_domain


# ── Project root ───────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[3]


# ── Session-scoped preflight fixture ───────────────────────────────────────

@pytest.fixture(scope="session")
def preflight_report():
    """Run pre-flight checks once per session. Skip all tests on fatal."""
    report = PreflightCheck(PROJECT_ROOT).check_all()
    if report.has_fatal:
        pytest.skip(f"Pre-flight failed: {report.fatal_summary}")
    return report


@pytest.fixture
def smoke_runner():
    """Create a SmokeRunner instance."""
    return SmokeRunner(project_root=PROJECT_ROOT)


# ═══════════════════════════════════════════════════════════════════════════
# PRE-FLIGHT TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestPreflight:
    """Verify all 4 repos are accessible."""

    def test_preflight_passes(self, preflight_report):
        """All pre-flight checks should pass or warn (no fatals)."""
        assert not preflight_report.has_fatal, preflight_report.fatal_summary

    def test_agent_contracts_installed(self, preflight_report):
        """agent-contracts must be importable."""
        contracts_check = next(
            (r for r in preflight_report.results if r.name == "package_agent_contracts"),
            None,
        )
        assert contracts_check is not None
        assert contracts_check.level in (CheckLevel.PASS, CheckLevel.INFO)

    def test_agent_tools_installed(self, preflight_report):
        """agent-tools must be importable."""
        tools_check = next(
            (r for r in preflight_report.results if r.name == "package_agent_tools"),
            None,
        )
        assert tools_check is not None
        assert tools_check.level in (CheckLevel.PASS, CheckLevel.INFO)

    def test_genesis_agents_exist(self, preflight_report):
        """Genesis agents directory must exist with agents."""
        genesis_check = next(
            (r for r in preflight_report.results if r.name == "genesis_agents"),
            None,
        )
        assert genesis_check is not None
        assert genesis_check.level == CheckLevel.PASS

    def test_shared_agents_exist(self, preflight_report):
        """Shared agents directory must exist with agents."""
        shared_check = next(
            (r for r in preflight_report.results if r.name == "shared_agents"),
            None,
        )
        assert shared_check is not None
        assert shared_check.level == CheckLevel.PASS


# ═══════════════════════════════════════════════════════════════════════════
# SINGLE-DOMAIN JOURNEY
# ═══════════════════════════════════════════════════════════════════════════

class TestFullJourney:
    """Full single-domain smoke test journey."""

    @pytest.mark.asyncio
    async def test_full_journey_passes(self, tmp_path, smoke_runner, preflight_report):
        """Full journey: scaffold → genesis → validate → run agents → run workflows."""
        report = await smoke_runner.run_full_journey(tmp_path)
        for step in report.steps:
            if not step.passed:
                pytest.fail(f"Step '{step.name}' failed: {step.message} | {step.detail}")
        assert report.passed

    @pytest.mark.asyncio
    async def test_scaffold_creates_valid_domain(self, tmp_path):
        """Scaffold should create a valid domain.yaml."""
        domain_dir = tmp_path / "test-domain"
        _scaffold_domain(domain_dir, "test_domain")

        assert (domain_dir / "domain.yaml").exists()
        assert (domain_dir / "agents").is_dir()
        assert (domain_dir / "workflows").is_dir()
        assert (domain_dir / "context").is_dir()

        data = yaml.safe_load((domain_dir / "domain.yaml").read_text())
        assert data["name"] == "test_domain"
        assert data["owner"] == "smoke-test"


# ═══════════════════════════════════════════════════════════════════════════
# CROSS-DOMAIN JOURNEY
# ═══════════════════════════════════════════════════════════════════════════

class TestCrossDomainJourney:
    """Multi-domain parallel workflow tests."""

    @pytest.mark.asyncio
    async def test_cross_domain_journey_passes(self, tmp_path, smoke_runner, preflight_report):
        """Cross-domain: 2 domains → parallel branches → gates → feedback."""
        report = await smoke_runner.run_cross_domain_journey(tmp_path)
        for step in report.steps:
            if not step.passed:
                pytest.fail(f"Step '{step.name}' failed: {step.message} | {step.detail}")
        assert report.passed

    def test_cross_domain_parallel_branches(self, smoke_runner):
        """feature_delivery.yaml must have parallel branches."""
        wf_path = PROJECT_ROOT / "workflows" / "cross_domain" / "feature_delivery.yaml"
        if not wf_path.exists():
            pytest.skip("feature_delivery.yaml not found")

        data = yaml.safe_load(wf_path.read_text())
        steps = data.get("steps", [])

        # Find steps that share the same depends_on (parallel branches)
        deps_map: dict[str, list[str]] = {}
        for step in steps:
            dep_key = ",".join(sorted(step.get("depends_on", [])))
            if dep_key:
                deps_map.setdefault(dep_key, []).append(step["name"])

        parallel_branches = [names for names in deps_map.values() if len(names) >= 2]
        assert len(parallel_branches) >= 1, "Expected at least 1 parallel branch pair"

    def test_cross_domain_feedback_loops(self, smoke_runner):
        """feature_delivery.yaml should have feedback loops."""
        wf_path = PROJECT_ROOT / "workflows" / "cross_domain" / "feature_delivery.yaml"
        if not wf_path.exists():
            pytest.skip("feature_delivery.yaml not found")

        data = yaml.safe_load(wf_path.read_text())
        loops = data.get("feedback_loops", [])
        assert len(loops) >= 1, "Expected at least 1 feedback loop"


# ═══════════════════════════════════════════════════════════════════════════
# DOMAIN HEALTH
# ═══════════════════════════════════════════════════════════════════════════

class TestDomainHealth:
    """Domain health checker tests."""

    @pytest.mark.asyncio
    async def test_domain_health_all_agents_complete(self, tmp_path, smoke_runner, preflight_report):
        """After genesis, all agents should have complete harness config."""
        # First run genesis to populate the domain
        report = await smoke_runner.run_full_journey(tmp_path)
        if not report.passed:
            pytest.skip("Full journey did not pass — skipping health check")

        domain_dir = tmp_path / "smoke-domain"
        health = smoke_runner.validate_domain_health(domain_dir)
        for agent in health.agents:
            assert agent.healthy, f"Agent {agent.name} has issues: {agent.issues}"
        assert health.score == 1.0

    def test_domain_health_acme_backend(self, smoke_runner):
        """Health check on real acme-backend domain (skipped if not available)."""
        acme_dir = PROJECT_ROOT.parent / "acme-backend"
        if not (acme_dir / "domain.yaml").exists():
            pytest.skip("acme-backend not available")

        health = smoke_runner.validate_domain_health(acme_dir)
        # acme-backend may not have agents yet (genesis not run with real API)
        if len(health.agents) == 0:
            pytest.skip("acme-backend has no generated agents yet (run genesis first)")
        # Report score but don't fail — acme may be in progress
        if not health.all_healthy:
            unhealthy = [a.name for a in health.agents if not a.healthy]
            pytest.xfail(f"acme-backend has incomplete agents: {unhealthy}")

    def test_each_generated_agent_has_harness_fields(self, tmp_path, smoke_runner):
        """Parametrized: every generated agent must have complete harness."""
        domain_dir = tmp_path / "health-check-domain"
        _scaffold_domain(domain_dir, "health_check")

        # Create a minimal agent to test
        agent_dir = domain_dir / "agents" / "TestAgent" / "v1"
        agent_dir.mkdir(parents=True)
        (agent_dir / "system_prompt.md").write_text("You are the TestAgent.")
        (agent_dir / "grading_criteria.yaml").write_text(
            yaml.dump({"criteria": [{"name": "quality", "type": "automated", "weight": 1.0}]})
        )
        (agent_dir / "agent_manifest.yaml").write_text(yaml.dump({
            "id": "health_check/TestAgent/v1",
            "name": "TestAgent",
            "version": "v1",
            "permissions": {"file_read": True},
            "constraints": {"max_tokens": 4096},
            "input_schema": {"type": "object", "properties": {"task": {"type": "string"}}},
            "output_schema": {"type": "object", "properties": {"result": {"type": "string"}}},
            "tools": [{"name": "file_read", "pack": "toolpack://core/filesystem"}],
            "harness": {
                "gate_condition": "status == success",
                "gate_on_fail": "retry",
                "grading_threshold": 0.7,
            },
        }))

        health = smoke_runner.validate_domain_health(domain_dir)
        for agent in health.agents:
            assert agent.healthy, f"Agent {agent.name} issues: {agent.issues}"


# ═══════════════════════════════════════════════════════════════════════════
# WORKFLOW DAG VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

class TestWorkflowDAG:
    """Workflow DAG structure tests."""

    def test_workflow_dag_valid(self, smoke_runner):
        """All cross-domain workflows should be valid DAGs."""
        wf_dir = PROJECT_ROOT / "workflows" / "cross_domain"
        if not wf_dir.is_dir():
            pytest.skip("No cross_domain workflows found")

        for wf_path in wf_dir.glob("*.yaml"):
            data = yaml.safe_load(wf_path.read_text())
            steps = data.get("steps", [])
            if not steps:
                continue

            # Check for cycles
            from harness.tests.smoke.smoke_runner import _check_dag_acyclic
            issues = _check_dag_acyclic(steps)
            assert not issues, f"{wf_path.name}: {issues}"


# ═══════════════════════════════════════════════════════════════════════════
# ZERO POLLUTION
# ═══════════════════════════════════════════════════════════════════════════

class TestZeroPollution:
    """Ensure smoke tests don't pollute any repos."""

    def test_no_temp_files_in_repos(self):
        """No smoke test artifacts should exist in any repo."""
        repos = [
            PROJECT_ROOT,
            PROJECT_ROOT.parent / "agent-contracts",
            PROJECT_ROOT.parent / "agent-tools",
            PROJECT_ROOT.parent / "acme-backend",
        ]
        for repo in repos:
            if not repo.is_dir():
                continue
            smoke_artifacts = list(repo.glob("smoke-domain"))
            smoke_artifacts += list(repo.glob("backend-domain"))
            smoke_artifacts += list(repo.glob("frontend-domain"))
            assert not smoke_artifacts, f"Smoke artifacts found in {repo}: {smoke_artifacts}"
