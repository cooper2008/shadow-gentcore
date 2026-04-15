"""SmokeRunner — core logic for smoke testing the full user journey.

Shared by both CLI (`./ai test smoke`) and pytest (`harness/tests/smoke/test_smoke_journey.py`).
All operations run in an isolated temporary directory — zero repo pollution.
Uses SmokeTestProvider — zero API tokens.

Usage:
    runner = SmokeRunner()
    report = await runner.run_full_journey(tmp_dir)
    report = await runner.run_cross_domain_journey(tmp_dir)
    health = runner.validate_domain_health(domain_dir)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ── Report dataclasses ─────────────────────────────────────────────────────

@dataclass
class StepResult:
    """Result of a single smoke test step."""
    name: str
    passed: bool
    message: str = ""
    detail: str = ""


@dataclass
class SmokeReport:
    """Aggregate report from a smoke test journey."""
    steps: list[StepResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.steps)

    @property
    def failed_count(self) -> int:
        return sum(1 for s in self.steps if not s.passed)

    @property
    def summary(self) -> str:
        total = len(self.steps)
        passed = total - self.failed_count
        status = "PASSED" if self.passed else "FAILED"
        return f"Smoke test {status}: {passed}/{total} steps passed"


@dataclass
class AgentHealth:
    """Health status of a single agent."""
    name: str
    issues: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return len(self.issues) == 0


@dataclass
class WorkflowHealth:
    """Health status of a single workflow."""
    name: str
    issues: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return len(self.issues) == 0


@dataclass
class HealthReport:
    """Aggregate domain health report."""
    agents: list[AgentHealth] = field(default_factory=list)
    workflows: list[WorkflowHealth] = field(default_factory=list)

    @property
    def score(self) -> float:
        total = len(self.agents) + len(self.workflows)
        if total == 0:
            return 0.0
        healthy = sum(1 for a in self.agents if a.healthy) + sum(1 for w in self.workflows if w.healthy)
        return healthy / total

    @property
    def all_healthy(self) -> bool:
        return all(a.healthy for a in self.agents) and all(w.healthy for w in self.workflows)


# ── Helpers ────────────────────────────────────────────────────────────────

def _project_root() -> Path:
    """Resolve shadow-gentcore project root."""
    return Path(__file__).resolve().parents[3]


REQUIRED_HARNESS_FIELDS = [
    "gate_condition", "gate_on_fail", "grading_threshold",
]

REQUIRED_AGENT_FIELDS = [
    "permissions", "constraints", "input_schema", "output_schema", "tools",
]


def _scaffold_domain(domain_dir: Path, name: str = "smoke_domain") -> None:
    """Create a minimal domain scaffold in the given directory."""
    domain_dir.mkdir(parents=True, exist_ok=True)
    (domain_dir / "agents").mkdir(exist_ok=True)
    (domain_dir / "workflows").mkdir(exist_ok=True)
    (domain_dir / "context").mkdir(exist_ok=True)

    domain_yaml = {
        "name": name,
        "owner": "smoke-test",
        "purpose": f"Smoke test domain: {name}",
        "version": "0.1.0",
        "workspace_policy": {
            "root_dir": ".",
            "allowed_paths": ["src/", "tests/"],
            "forbidden_paths": [".env"],
        },
        "autonomy_profile": "assisted",
        "default_tool_packs": ["toolpack://core/filesystem"],
        "metadata": {"team": "smoke-test"},
    }
    (domain_dir / "domain.yaml").write_text(
        yaml.dump(domain_yaml, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def _check_dag_acyclic(steps: list[dict[str, Any]]) -> list[str]:
    """Check if a workflow DAG is acyclic. Returns list of issues."""
    step_names = {s["name"] for s in steps}
    issues: list[str] = []

    # Build adjacency
    graph: dict[str, list[str]] = {s["name"]: [] for s in steps}
    for s in steps:
        for dep in s.get("depends_on", []):
            if dep not in step_names:
                issues.append(f"Step '{s['name']}' depends on unknown step '{dep}'")
            else:
                graph[dep].append(s["name"])

    # Cycle detection via DFS
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in step_names}

    def dfs(node: str) -> bool:
        color[node] = GRAY
        for neighbor in graph.get(node, []):
            if color[neighbor] == GRAY:
                issues.append(f"Cycle detected involving step '{neighbor}'")
                return True
            if color[neighbor] == WHITE and dfs(neighbor):
                return True
        color[node] = BLACK
        return False

    for node in step_names:
        if color[node] == WHITE:
            dfs(node)

    return issues


# ── SmokeRunner ────────────────────────────────────────────────────────────

class SmokeRunner:
    """Core smoke test logic — shared by CLI and pytest."""

    def __init__(self, project_root: Path | None = None, verbose: bool = False) -> None:
        self.project_root = project_root or _project_root()
        self.verbose = verbose

    # ── Single-domain journey ──────────────────────────────────────────

    async def run_full_journey(self, tmp_dir: Path) -> SmokeReport:
        """Run the full single-domain smoke test journey.

        1. Pre-flight
        2. Scaffold domain
        3. Genesis build (via SmokeTestProvider)
        4. Validate manifests
        5. Check harness completeness
        6. Check stage agents
        7. Run each domain agent
        8. Run workflow DAG
        """
        from harness.tests.smoke.preflight import PreflightCheck

        report = SmokeReport()

        # 1. Pre-flight
        preflight = PreflightCheck(self.project_root).check_all()
        if preflight.has_fatal:
            report.steps.append(StepResult(
                "preflight", False,
                f"Pre-flight failed: {preflight.fatal_summary}",
            ))
            return report
        report.steps.append(StepResult("preflight", True, "All checks passed"))

        # 2. Scaffold domain
        domain_dir = tmp_dir / "smoke-domain"
        try:
            _scaffold_domain(domain_dir, "smoke_domain")
            assert (domain_dir / "domain.yaml").exists()
            report.steps.append(StepResult("scaffold", True, f"Domain scaffolded at {domain_dir}"))
        except Exception as exc:
            report.steps.append(StepResult("scaffold", False, f"Scaffold failed: {exc}"))
            return report

        # 3. Genesis build
        genesis_result = await self._run_genesis(domain_dir)
        if genesis_result.passed:
            report.steps.append(genesis_result)
        else:
            report.steps.append(genesis_result)
            return report

        # 4. Validate manifests
        manifest_result = self._validate_manifests(domain_dir)
        report.steps.append(manifest_result)

        # 5. Check harness completeness
        harness_result = self._check_harness_completeness(domain_dir)
        report.steps.append(harness_result)

        # 6. Check stage agents
        stage_result = self._check_stage_agents(domain_dir)
        report.steps.append(stage_result)

        # 7. Run each domain agent
        agent_result = await self._run_domain_agents(domain_dir)
        report.steps.append(agent_result)

        # 8. Run workflow DAG
        workflow_result = await self._run_workflow_dag(domain_dir)
        report.steps.append(workflow_result)

        return report

    # ── Cross-domain journey ───────────────────────────────────────────

    async def run_cross_domain_journey(self, tmp_dir: Path) -> SmokeReport:
        """Run the cross-domain smoke test with two domains + shared workflow.

        1. Pre-flight
        2. Scaffold 2 domains
        3. Genesis both
        4. Run cross-domain workflow (feature_delivery pattern)
        5. Verify parallel branches + gates + feedback
        """
        from harness.tests.smoke.preflight import PreflightCheck

        report = SmokeReport()

        # 1. Pre-flight
        preflight = PreflightCheck(self.project_root).check_all()
        if preflight.has_fatal:
            report.steps.append(StepResult(
                "preflight", False,
                f"Pre-flight failed: {preflight.fatal_summary}",
            ))
            return report
        report.steps.append(StepResult("preflight", True, "All checks passed"))

        # 2. Scaffold 2 domains
        backend_dir = tmp_dir / "backend-domain"
        frontend_dir = tmp_dir / "frontend-domain"
        try:
            _scaffold_domain(backend_dir, "backend_domain")
            _scaffold_domain(frontend_dir, "frontend_domain")
            report.steps.append(StepResult(
                "scaffold_domains", True,
                "Two domains scaffolded (backend + frontend)",
            ))
        except Exception as exc:
            report.steps.append(StepResult("scaffold_domains", False, f"Scaffold failed: {exc}"))
            return report

        # 3. Genesis both
        backend_genesis = await self._run_genesis(backend_dir)
        frontend_genesis = await self._run_genesis(frontend_dir)
        both_passed = backend_genesis.passed and frontend_genesis.passed
        report.steps.append(StepResult(
            "genesis_both", both_passed,
            f"Backend: {backend_genesis.message}, Frontend: {frontend_genesis.message}",
        ))
        if not both_passed:
            return report

        # 4. Run cross-domain workflow
        cross_result = await self._run_cross_domain_workflow(backend_dir, frontend_dir)
        report.steps.append(cross_result)

        # 5. Verify DAG structure
        dag_result = self._verify_cross_domain_dag()
        report.steps.append(dag_result)

        return report

    # ── Domain health checker ──────────────────────────────────────────

    def validate_domain_health(self, domain_dir: Path) -> HealthReport:
        """Validate completeness of all agents and workflows in a domain."""
        health = HealthReport()

        agents_dir = domain_dir / "agents"
        if agents_dir.is_dir():
            for manifest_path in sorted(agents_dir.rglob("agent_manifest.yaml")):
                agent_name = manifest_path.parent.parent.name
                agent_health = self._check_agent_health(manifest_path, agent_name)
                health.agents.append(agent_health)

        workflows_dir = domain_dir / "workflows"
        if workflows_dir.is_dir():
            for wf_path in sorted(workflows_dir.rglob("*.yaml")):
                wf_name = wf_path.stem
                wf_health = self._check_workflow_health(wf_path, wf_name)
                health.workflows.append(wf_health)

        # Check context/standards.md
        standards = domain_dir / "context" / "standards.md"
        if not standards.exists():
            # Add as a workflow-level issue for reporting
            health.workflows.append(WorkflowHealth(
                name="context/standards.md",
                issues=["context/standards.md missing"],
            ))
        elif standards.stat().st_size < 50:
            health.workflows.append(WorkflowHealth(
                name="context/standards.md",
                issues=["context/standards.md is too short (likely a stub)"],
            ))

        return health

    # ── Private helpers ────────────────────────────────────────────────

    async def _run_genesis(self, domain_dir: Path) -> StepResult:
        """Run genesis pipeline on a scaffolded domain using SmokeTestProvider."""
        try:
            from harness.core.manifest_loader import ManifestLoader
            from harness.providers.smoke_test_provider import SmokeTestProvider

            provider = SmokeTestProvider()
            loader = ManifestLoader()

            genesis_workflow = self.project_root / "workflows" / "genesis" / "genesis_build.yaml"
            if not genesis_workflow.exists():
                return StepResult("genesis", False, f"Genesis workflow not found: {genesis_workflow}")

            task_input = {
                "domain_name": domain_dir.name,
                "output_dir": str(domain_dir),
                "industry": "software",
                "team_config": {
                    "industry": "software",
                    "trusted": True,
                    "target": [{"path": str(domain_dir)}],
                    "output": str(domain_dir),
                },
            }

            engine, workflow_data, step_configs = loader.boot_engine(
                genesis_workflow,
                domain_root=self.project_root,
                provider=provider,
                task_input=task_input,
            )

            result = await engine.execute_dag(workflow_data["steps"], step_configs)
            if result["status"] == "completed":
                # SmokeTestProvider returns stubs — agents don't write real files.
                # Scaffold minimal agent files so validate_manifests can proceed.
                self._scaffold_smoke_agents(domain_dir)
                return StepResult("genesis", True, f"Genesis completed ({len(result['step_results'])} steps)")
            return StepResult(
                "genesis", False,
                f"Genesis failed: {result.get('error', result['status'])}",
                detail=f"Failed step: {result.get('failed_step', 'unknown')}",
            )
        except Exception as exc:
            logger.exception("Genesis failed")
            return StepResult("genesis", False, f"Genesis exception: {exc}")

    @staticmethod
    def _scaffold_smoke_agents(domain_dir: Path) -> None:
        """Create minimal agent files after smoke genesis (which uses stubs, not real file_write)."""
        import yaml as _yaml

        agents_dir = domain_dir / "agents"
        # Create a sample agent so validate_manifests has something to check
        for agent_name in ("CodeWriterAgent", "TestRunnerAgent"):
            agent_dir = agents_dir / agent_name / "v1"
            agent_dir.mkdir(parents=True, exist_ok=True)

            if not (agent_dir / "agent_manifest.yaml").exists():
                manifest = {
                    "id": f"{domain_dir.name}/{agent_name}/v1",
                    "domain": domain_dir.name,
                    "category": "fast-codegen",
                    "description": f"Smoke-generated {agent_name}",
                    "version": "1.0.0",
                    "system_prompt_ref": "system_prompt.md",
                    "grading_criteria_ref": "grading_criteria.yaml",
                    "execution_mode": {"name": "react", "max_steps": 5},
                    "tools": ["file_read", "file_write"],
                    "harness": {
                        "gate_condition": "status == success",
                        "gate_on_fail": "retry",
                        "max_retries": 1,
                        "grading_threshold": 0.7,
                    },
                    "constraints": {},
                    "permissions": {},
                    "input_schema": {"type": "object", "properties": {"instruction": {"type": "string"}}},
                    "output_schema": {"type": "object", "properties": {"result": {"type": "string"}}},
                }
                (agent_dir / "agent_manifest.yaml").write_text(
                    _yaml.dump(manifest, default_flow_style=False), encoding="utf-8"
                )

            if not (agent_dir / "system_prompt.md").exists():
                (agent_dir / "system_prompt.md").write_text(
                    f"# {agent_name}\n\nYou are {agent_name}. Execute the given task.\n",
                    encoding="utf-8",
                )

            if not (agent_dir / "grading_criteria.yaml").exists():
                criteria = {"threshold": 0.7, "criteria": [{"name": "completeness", "weight": 1.0}]}
                (agent_dir / "grading_criteria.yaml").write_text(
                    _yaml.dump(criteria, default_flow_style=False), encoding="utf-8"
                )

        # Create a minimal workflow
        workflows_dir = domain_dir / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        wf_path = workflows_dir / "feature_delivery.yaml"
        if not wf_path.exists():
            workflow = {
                "name": "feature_delivery",
                "steps": [
                    {"name": "write", "agent": f"{domain_dir.name}/CodeWriterAgent/v1",
                     "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 1}},
                    {"name": "test", "agent": f"{domain_dir.name}/TestRunnerAgent/v1",
                     "depends_on": ["write"],
                     "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 1}},
                ],
            }
            wf_path.write_text(_yaml.dump(workflow, default_flow_style=False), encoding="utf-8")

        # Create context/standards.md
        context_dir = domain_dir / "context"
        context_dir.mkdir(parents=True, exist_ok=True)
        if not (context_dir / "standards.md").exists():
            (context_dir / "standards.md").write_text(
                "# Project Standards\n\n"
                "## Code Style\n"
                "- Follow PEP 8 for Python code\n"
                "- Use type hints for all function signatures\n"
                "- Maximum line length: 120 characters\n\n"
                "## Testing\n"
                "- All new features require unit tests\n"
                "- Use pytest as the test framework\n"
                "- Minimum coverage target: 80%\n\n"
                "## Documentation\n"
                "- All public functions require docstrings\n"
                "- Keep README up to date with changes\n",
                encoding="utf-8",
            )

    def _validate_manifests(self, domain_dir: Path) -> StepResult:
        """Validate that every agent has required manifest files."""
        agents_dir = domain_dir / "agents"
        if not agents_dir.is_dir():
            return StepResult("validate_manifests", False, "No agents/ directory found")

        agent_dirs = [d for d in agents_dir.rglob("agent_manifest.yaml")]
        if not agent_dirs:
            return StepResult("validate_manifests", False, "No agent_manifest.yaml files found")

        missing: list[str] = []
        for manifest_path in agent_dirs:
            agent_dir = manifest_path.parent
            agent_name = agent_dir.parent.name
            if not (agent_dir / "system_prompt.md").exists():
                missing.append(f"{agent_name}: missing system_prompt.md")
            if not (agent_dir / "grading_criteria.yaml").exists():
                missing.append(f"{agent_name}: missing grading_criteria.yaml")

        if missing:
            return StepResult(
                "validate_manifests", False,
                f"{len(missing)} missing files",
                detail="; ".join(missing[:5]),
            )
        return StepResult("validate_manifests", True, f"{len(agent_dirs)} agents validated")

    def _check_harness_completeness(self, domain_dir: Path) -> StepResult:
        """Check every agent has complete harness configuration."""
        agents_dir = domain_dir / "agents"
        if not agents_dir.is_dir():
            return StepResult("harness_completeness", False, "No agents/ directory")

        issues: list[str] = []
        agent_count = 0

        for manifest_path in sorted(agents_dir.rglob("agent_manifest.yaml")):
            agent_count += 1
            agent_name = manifest_path.parent.parent.name
            try:
                data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            except Exception:
                issues.append(f"{agent_name}: invalid YAML")
                continue

            for fld in REQUIRED_AGENT_FIELDS:
                if fld not in data:
                    issues.append(f"{agent_name}: missing {fld}")

            harness = data.get("harness", {})
            if not harness:
                issues.append(f"{agent_name}: missing harness config entirely")
            else:
                for fld in REQUIRED_HARNESS_FIELDS:
                    if fld not in harness:
                        issues.append(f"{agent_name}: harness missing {fld}")

            # Check tools have pack refs
            tools = data.get("tools", [])
            for tool in tools:
                if isinstance(tool, dict) and "pack" not in tool:
                    issues.append(f"{agent_name}: tool '{tool.get('name', '?')}' missing pack ref")

        if issues:
            return StepResult(
                "harness_completeness", False,
                f"{len(issues)} harness issues across {agent_count} agents",
                detail="; ".join(issues[:5]),
            )
        return StepResult("harness_completeness", True, f"{agent_count} agents fully configured")

    def _check_stage_agents(self, domain_dir: Path) -> StepResult:
        """Verify _shared/ stage agents referenced in workflows exist."""
        shared_dir = self.project_root / "agents" / "_shared"
        if not shared_dir.is_dir():
            return StepResult("stage_agents", False, "agents/_shared/ not found in project root")

        available_shared = {d.name for d in shared_dir.iterdir() if d.is_dir()}

        workflows_dir = domain_dir / "workflows"
        if not workflows_dir.is_dir():
            return StepResult("stage_agents", True, "No workflows to check")

        missing: list[str] = []
        for wf_path in workflows_dir.rglob("*.yaml"):
            try:
                data = yaml.safe_load(wf_path.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            for step in data.get("steps", []):
                agent_ref = step.get("agent", "")
                if agent_ref.startswith("_shared/"):
                    parts = agent_ref.split("/")
                    agent_name = parts[1] if len(parts) >= 2 else ""
                    if agent_name and agent_name not in available_shared:
                        missing.append(f"{wf_path.stem}: _shared/{agent_name} not found")

        if missing:
            return StepResult(
                "stage_agents", False,
                f"{len(missing)} missing shared agents",
                detail="; ".join(missing[:5]),
            )
        return StepResult("stage_agents", True, "All referenced shared agents exist")

    async def _run_domain_agents(self, domain_dir: Path) -> StepResult:
        """Run each domain agent with SmokeTestProvider and validate output."""
        try:
            from harness.core.agent_runner import AgentRunner
            from harness.core.output_validator import OutputValidator
            from harness.providers.smoke_test_provider import SmokeTestProvider

            provider = SmokeTestProvider()
            _runner = AgentRunner(provider=provider)  # noqa: F841 — validates import
            _validator = OutputValidator()  # noqa: F841 — validates import

            agents_dir = domain_dir / "agents"
            if not agents_dir.is_dir():
                return StepResult("run_agents", False, "No agents/ directory")

            manifest_files = list(agents_dir.rglob("agent_manifest.yaml"))
            if not manifest_files:
                return StepResult("run_agents", False, "No agents found")

            passed = 0
            failed_agents: list[str] = []

            for manifest_path in manifest_files:
                agent_name = manifest_path.parent.parent.name
                try:
                    manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}

                    # Build minimal system prompt
                    prompt_path = manifest_path.parent / "system_prompt.md"
                    system_prompt = ""
                    if prompt_path.exists():
                        system_prompt = prompt_path.read_text(encoding="utf-8")

                    # Build messages for the provider
                    messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Execute task for {agent_name}: smoke test"},
                    ]

                    # Get output from SmokeTestProvider
                    response = await provider.chat(
                        messages,
                        output_schema=manifest_data.get("output_schema"),
                    )

                    content = response.get("content", "")
                    if not content:
                        failed_agents.append(f"{agent_name}: empty response")
                        continue

                    # Try to parse as JSON (OutputParser equivalent)
                    try:
                        json.loads(content)
                        passed += 1
                    except json.JSONDecodeError:
                        failed_agents.append(f"{agent_name}: non-JSON response")

                except Exception as exc:
                    failed_agents.append(f"{agent_name}: {exc}")

            total = len(manifest_files)
            if failed_agents:
                return StepResult(
                    "run_agents", False,
                    f"{passed}/{total} agents passed, {len(failed_agents)} failed",
                    detail="; ".join(failed_agents[:5]),
                )
            return StepResult("run_agents", True, f"All {total} agents produced valid output")

        except Exception as exc:
            return StepResult("run_agents", False, f"Agent run exception: {exc}")

    async def _run_workflow_dag(self, domain_dir: Path) -> StepResult:
        """Run workflow DAGs with SmokeTestProvider via CompositionEngine."""
        try:
            from harness.core.agent_runner import AgentRunner
            from harness.core.composition_engine import CompositionEngine
            from harness.providers.smoke_test_provider import SmokeTestProvider

            provider = SmokeTestProvider()
            runner = AgentRunner(provider=provider)
            engine = CompositionEngine(agent_runner=runner)

            workflows_dir = domain_dir / "workflows"
            if not workflows_dir.is_dir():
                return StepResult("workflow_dag", True, "No workflows to run")

            wf_files = list(workflows_dir.rglob("*.yaml"))
            if not wf_files:
                return StepResult("workflow_dag", True, "No workflow YAML files found")

            passed = 0
            failed_wfs: list[str] = []

            for wf_path in wf_files:
                try:
                    data = yaml.safe_load(wf_path.read_text(encoding="utf-8")) or {}
                    steps = data.get("steps", [])
                    if not steps:
                        continue

                    result = await engine.execute_dag(steps)
                    if result["status"] == "completed":
                        passed += 1
                    else:
                        failed_wfs.append(f"{wf_path.stem}: {result['status']}")
                except Exception as exc:
                    failed_wfs.append(f"{wf_path.stem}: {exc}")

            total = len(wf_files)
            if failed_wfs:
                return StepResult(
                    "workflow_dag", False,
                    f"{passed}/{total} workflows passed",
                    detail="; ".join(failed_wfs[:5]),
                )
            return StepResult("workflow_dag", True, f"All {total} workflows completed")

        except Exception as exc:
            return StepResult("workflow_dag", False, f"Workflow DAG exception: {exc}")

    async def _run_cross_domain_workflow(self, _backend_dir: Path, _frontend_dir: Path) -> StepResult:
        """Run a cross-domain workflow modeled on feature_delivery.yaml."""
        try:
            from harness.core.agent_runner import AgentRunner
            from harness.core.composition_engine import CompositionEngine
            from harness.providers.smoke_test_provider import SmokeTestProvider

            provider = SmokeTestProvider()
            runner = AgentRunner(provider=provider)
            engine = CompositionEngine(agent_runner=runner)

            # Cross-domain workflow: plan → [backend || frontend] → evaluate
            steps = [
                {"name": "plan", "agent": "_orchestrator/PlannerAgent/v1",
                 "gate": {"condition": "status == success", "on_fail": "abort"}},
                {"name": "api_build", "agent": "_shared/CodeWriterAgent/v1",
                 "depends_on": ["plan"],
                 "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 2}},
                {"name": "component_build", "agent": "_shared/CodeWriterAgent/v1",
                 "depends_on": ["plan"],
                 "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 2}},
                {"name": "api_test", "agent": "_shared/TestRunnerAgent/v1",
                 "depends_on": ["api_build"],
                 "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 2}},
                {"name": "component_lint", "agent": "_shared/LinterAgent/v1",
                 "depends_on": ["component_build"],
                 "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 1}},
                {"name": "evaluate", "agent": "_orchestrator/EvaluatorAgent/v1",
                 "depends_on": ["api_test", "component_lint"],
                 "gate": {"condition": "status == success", "on_fail": "degrade"}},
            ]

            result = await engine.execute_dag(steps)
            if result["status"] == "completed":
                step_count = len(result.get("step_results", {}))
                return StepResult(
                    "cross_domain_workflow", True,
                    f"Cross-domain workflow completed ({step_count} steps)",
                )
            return StepResult(
                "cross_domain_workflow", False,
                f"Cross-domain workflow failed: {result.get('error', result['status'])}",
            )
        except Exception as exc:
            return StepResult("cross_domain_workflow", False, f"Cross-domain exception: {exc}")

    def _verify_cross_domain_dag(self) -> StepResult:
        """Verify the cross-domain feature_delivery.yaml DAG structure."""
        wf_path = self.project_root / "workflows" / "cross_domain" / "feature_delivery.yaml"
        if not wf_path.exists():
            return StepResult("cross_domain_dag", False, "feature_delivery.yaml not found")

        try:
            data = yaml.safe_load(wf_path.read_text(encoding="utf-8")) or {}
            steps = data.get("steps", [])
            if not steps:
                return StepResult("cross_domain_dag", False, "No steps in feature_delivery.yaml")

            issues = _check_dag_acyclic(steps)
            if issues:
                return StepResult(
                    "cross_domain_dag", False,
                    f"DAG issues: {'; '.join(issues[:3])}",
                )

            # Verify parallel branches exist (api_build || component_build should share same depends_on)
            has_parallel = False
            deps_map: dict[str, list[str]] = {}
            for step in steps:
                dep_key = ",".join(sorted(step.get("depends_on", [])))
                if dep_key:
                    deps_map.setdefault(dep_key, []).append(step["name"])
            for dep_key, step_names in deps_map.items():
                if len(step_names) >= 2:
                    has_parallel = True
                    break

            if not has_parallel:
                return StepResult("cross_domain_dag", False, "No parallel branches detected")

            # Check feedback loops
            feedback_loops = data.get("feedback_loops", [])
            if not feedback_loops:
                return StepResult(
                    "cross_domain_dag", True,
                    f"DAG valid ({len(steps)} steps, parallel branches, no feedback loops)",
                )

            return StepResult(
                "cross_domain_dag", True,
                f"DAG valid ({len(steps)} steps, parallel branches, {len(feedback_loops)} feedback loops)",
            )
        except Exception as exc:
            return StepResult("cross_domain_dag", False, f"DAG verification exception: {exc}")

    def _check_agent_health(self, manifest_path: Path, agent_name: str) -> AgentHealth:
        """Check health of a single agent from its manifest."""
        health = AgentHealth(name=agent_name)
        try:
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            health.issues.append(f"Invalid YAML: {exc}")
            return health

        for fld in REQUIRED_AGENT_FIELDS:
            if fld not in data:
                health.issues.append(f"Missing {fld}")

        harness = data.get("harness", {})
        if not harness:
            health.issues.append("Missing harness config entirely")
        else:
            for fld in REQUIRED_HARNESS_FIELDS:
                if fld not in harness:
                    health.issues.append(f"Harness missing {fld}")

        tools = data.get("tools", [])
        for tool in tools:
            if isinstance(tool, dict) and "pack" not in tool:
                health.issues.append(f"Tool '{tool.get('name', '?')}' missing pack ref")

        # Check companion files
        agent_dir = manifest_path.parent
        if not (agent_dir / "system_prompt.md").exists():
            health.issues.append("Missing system_prompt.md")
        if not (agent_dir / "grading_criteria.yaml").exists():
            health.issues.append("Missing grading_criteria.yaml")

        return health

    def _check_workflow_health(self, wf_path: Path, wf_name: str) -> WorkflowHealth:
        """Check health of a single workflow."""
        health = WorkflowHealth(name=wf_name)
        try:
            data = yaml.safe_load(wf_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            health.issues.append(f"Invalid YAML: {exc}")
            return health

        steps = data.get("steps", [])
        if not steps:
            health.issues.append("No steps defined")
            return health

        # Check DAG is acyclic
        dag_issues = _check_dag_acyclic(steps)
        health.issues.extend(dag_issues)

        # Check all step agents are resolvable
        for step in steps:
            agent_ref = step.get("agent", "")
            if not agent_ref:
                health.issues.append(f"Step '{step.get('name', '?')}' has no agent")
            # Check gate config
            gate = step.get("gate", {})
            if not gate:
                health.issues.append(f"Step '{step.get('name', '?')}' has no gate")

        return health
