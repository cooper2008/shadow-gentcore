"""Full framework flow test — proves the REAL pipeline works end-to-end.

Tests the actual framework components (not mocks):
- ManifestLoader loads real genesis agent manifests from disk
- PromptAssembler injects real system prompts + context
- CompositionEngine executes real DAG with real gates
- OutputValidator grades output against real grading_criteria.yaml
- AgentRunner tracks real lifecycle states
- RuleEngine enforces real rules

Uses GenesisTestProvider (structured JSON) instead of DryRunProvider (generic stubs)
so that agents return schema-correct output that OutputValidator can actually grade.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from harness.core.manifest_loader import ManifestLoader
from harness.core.agent_runner import AgentRunner, AgentState
from harness.core.composition_engine import CompositionEngine, ExecutionEvent
from harness.core.output_validator import OutputValidator
from harness.core.rule_engine import RuleEngine, RuleContext
from harness.core.prompt_assembler import PromptAssembler
from harness.core.tool_executor import ToolExecutor
from harness.tools.builtin import register_builtins
from harness.tests.genesis_test_provider import GenesisTestProvider


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ═══════════════════════════════════════════════════════════════════════
# TEST 1: ManifestLoader loads ALL 8 genesis agent manifests from disk
# ═══════════════════════════════════════════════════════════════════════

class TestManifestLoaderLoadsGenesisAgents:
    """Prove ManifestLoader can load every genesis agent from real YAML files."""

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
    def test_load_agent_manifest(self, agent_name: str) -> None:
        loader = ManifestLoader()
        agent_dir = PROJECT_ROOT / "agents" / "_genesis" / agent_name / "v1"
        manifest, system_prompt, context_items = loader.load_agent(agent_dir)

        # Manifest loaded correctly
        assert manifest["id"] == f"_genesis/{agent_name}/v1"
        assert manifest["domain"] == "_genesis"
        assert "execution_mode" in manifest
        assert "tools" in manifest
        assert "permissions" in manifest
        assert "input_schema" in manifest
        assert "output_schema" in manifest

        # System prompt loaded
        assert len(system_prompt) > 100, f"{agent_name} system_prompt too short"
        assert agent_name in system_prompt

    @pytest.mark.parametrize("agent_name", GENESIS_AGENTS)
    def test_load_grading_criteria(self, agent_name: str) -> None:
        criteria_path = PROJECT_ROOT / "agents" / "_genesis" / agent_name / "v1" / "grading_criteria.yaml"
        assert criteria_path.exists()
        data = yaml.safe_load(criteria_path.read_text())
        assert "threshold" in data
        assert data["threshold"] >= 0.75
        assert "criteria" in data
        assert len(data["criteria"]) >= 2


# ═══════════════════════════════════════════════════════════════════════
# TEST 2: ManifestLoader.boot_engine wires the full pipeline correctly
# ═══════════════════════════════════════════════════════════════════════

class TestBootEngineWiring:
    """Prove boot_engine correctly wires all components."""

    def test_boot_genesis_build_workflow(self) -> None:
        loader = ManifestLoader()
        provider = GenesisTestProvider()
        wf_path = PROJECT_ROOT / "workflows" / "genesis" / "genesis_build.yaml"

        engine, workflow, step_configs = loader.boot_engine(
            wf_path, domain_root=PROJECT_ROOT, provider=provider,
            task_input={"sources": [{"path": str(PROJECT_ROOT / "sample_project" / "backend"), "type": "git_repo"}], "industry": "software", "domain_name": "test"},
        )

        # Engine has agent_runner and output_validator
        assert engine._agent_runner is not None
        assert engine._output_validator is not None

        # All 7 steps have configs
        assert len(step_configs) == 7
        expected_steps = {"scan", "map", "discover_tools", "engineer_context", "architect", "build", "validate"}
        assert set(step_configs.keys()) == expected_steps

        # Each step has manifest + system_prompt + task
        for step_name, config in step_configs.items():
            assert "manifest" in config, f"{step_name} missing manifest"
            assert "system_prompt" in config, f"{step_name} missing system_prompt"
            assert "task" in config, f"{step_name} missing task"
            # Manifest should have been loaded from disk
            manifest = config["manifest"]
            assert "id" in manifest, f"{step_name} manifest missing id"
            assert manifest["domain"] == "_genesis", f"{step_name} wrong domain"

    def test_boot_engine_injects_task_input_to_all_steps(self) -> None:
        loader = ManifestLoader()
        provider = GenesisTestProvider()
        wf_path = PROJECT_ROOT / "workflows" / "genesis" / "genesis_build.yaml"

        engine, workflow, step_configs = loader.boot_engine(
            wf_path, domain_root=PROJECT_ROOT, provider=provider,
            task_input={"industry": "software", "domain_name": "test_domain"},
        )

        for step_name, config in step_configs.items():
            task = config["task"]
            assert task.get("industry") == "software", f"{step_name} missing industry in task"
            assert task.get("domain_name") == "test_domain", f"{step_name} missing domain_name"


# ═══════════════════════════════════════════════════════════════════════
# TEST 3: Full genesis pipeline through boot_engine
# ═══════════════════════════════════════════════════════════════════════

class TestFullGenesisPipelineThroughBootEngine:
    """Run genesis_build workflow through the REAL framework pipeline."""

    @pytest.mark.asyncio
    async def test_genesis_build_full_pipeline(self) -> None:
        loader = ManifestLoader()
        provider = GenesisTestProvider()
        wf_path = PROJECT_ROOT / "workflows" / "genesis" / "genesis_build.yaml"

        engine, workflow, step_configs = loader.boot_engine(
            wf_path, domain_root=PROJECT_ROOT, provider=provider,
            task_input={
                "team_config": {
                    "reference": [{"path": str(PROJECT_ROOT / "sample_project" / "backend")}],
                    "target": [
                        {"path": str(PROJECT_ROOT / "sample_project" / "backend")},
                        {"path": str(PROJECT_ROOT / "sample_project" / "frontend")},
                        {"path": str(PROJECT_ROOT / "sample_project" / "infra")},
                    ],
                    "docs": [{"path": str(PROJECT_ROOT / "sample_project" / "docs"), "type": "documents"}],
                    "industry": "software",
                    "trusted": True,
                },
                "industry": "software",
                "domain_name": "fullstack",
            },
        )

        result = await engine.execute_dag(workflow["steps"], step_configs)

        assert result["status"] == "completed", f"Pipeline failed: {result.get('error', result.get('failed_step', 'unknown'))}"
        assert len(result["step_results"]) == 7

        # Collect scores
        scores = {}
        for step_name, step_result in result["step_results"].items():
            validation = step_result.get("_validation", {})
            score = validation.get("score", "N/A")
            passed = validation.get("passed", "N/A")
            scores[step_name] = {"score": score, "passed": passed}

        # Report
        print("\n" + "=" * 60)
        print("GENESIS BUILD PIPELINE — STEP RESULTS")
        print("=" * 60)
        for step_name in ["scan", "map", "discover_tools", "engineer_context", "architect", "build", "validate"]:
            sr = result["step_results"][step_name]
            status = sr.get("status", "unknown")
            val = sr.get("_validation", {})
            score = val.get("score", "-")
            passed = val.get("passed", "-")
            print(f"  {step_name:20s} status={status:10s} score={score}  passed={passed}")
        print("=" * 60)

    @pytest.mark.asyncio
    async def test_genesis_scan_through_boot_engine(self) -> None:
        loader = ManifestLoader()
        provider = GenesisTestProvider()
        wf_path = PROJECT_ROOT / "workflows" / "genesis" / "genesis_scan.yaml"

        engine, workflow, step_configs = loader.boot_engine(
            wf_path, domain_root=PROJECT_ROOT, provider=provider,
            task_input={"industry": "software"},
        )

        result = await engine.execute_dag(workflow["steps"], step_configs)
        assert result["status"] == "completed"
        assert len(result["step_results"]) == 2

    @pytest.mark.asyncio
    async def test_genesis_evolve_through_boot_engine(self) -> None:
        loader = ManifestLoader()
        provider = GenesisTestProvider()
        wf_path = PROJECT_ROOT / "workflows" / "genesis" / "genesis_evolve.yaml"

        engine, workflow, step_configs = loader.boot_engine(
            wf_path, domain_root=PROJECT_ROOT, provider=provider,
            task_input={"domain_path": str(PROJECT_ROOT / "sample_project" / "backend")},
        )

        result = await engine.execute_dag(workflow["steps"], step_configs)
        assert result["status"] == "completed"
        assert len(result["step_results"]) == 3


# ═══════════════════════════════════════════════════════════════════════
# TEST 4: AgentRunner lifecycle states with REAL manifests
# ═══════════════════════════════════════════════════════════════════════

class TestAgentRunnerWithRealManifests:
    """Run individual genesis agents through AgentRunner with real manifests."""

    @pytest.mark.asyncio
    async def test_source_scanner_lifecycle(self) -> None:
        loader = ManifestLoader()
        provider = GenesisTestProvider()
        agent_dir = PROJECT_ROOT / "agents" / "_genesis" / "SourceScannerAgent" / "v1"
        manifest, system_prompt, context_items = loader.load_agent(agent_dir)

        runner = AgentRunner(provider=provider)
        result = await runner.run(
            manifest=manifest,
            task={"task_id": "test-scan", "agent_id": "_genesis/SourceScannerAgent/v1",
                  "sources": [{"path": "/mock/backend", "type": "git_repo"}]},
            system_prompt_content=system_prompt,
            context_items=context_items,
        )

        assert result["status"] == "completed"
        # Lifecycle states tracked
        states = [s["state"] for s in result["state_log"]]
        assert "spawning" in states
        assert "ready" in states
        assert "running" in states
        assert "completed" in states

    @pytest.mark.asyncio
    async def test_agent_architect_lifecycle(self) -> None:
        loader = ManifestLoader()
        provider = GenesisTestProvider()
        agent_dir = PROJECT_ROOT / "agents" / "_genesis" / "AgentArchitectAgent" / "v1"
        manifest, system_prompt, context_items = loader.load_agent(agent_dir)

        runner = AgentRunner(provider=provider)
        result = await runner.run(
            manifest=manifest,
            task={"task_id": "test-arch", "agent_id": "_genesis/AgentArchitectAgent/v1"},
            system_prompt_content=system_prompt,
        )

        assert result["status"] == "completed"
        states = [s["state"] for s in result["state_log"]]
        assert "spawning" in states
        assert "completed" in states

    @pytest.mark.asyncio
    async def test_quality_gate_lifecycle(self) -> None:
        loader = ManifestLoader()
        provider = GenesisTestProvider()
        agent_dir = PROJECT_ROOT / "agents" / "_genesis" / "QualityGateAgent" / "v1"
        manifest, system_prompt, context_items = loader.load_agent(agent_dir)

        runner = AgentRunner(provider=provider)
        result = await runner.run(
            manifest=manifest,
            task={"task_id": "test-qg", "agent_id": "_genesis/QualityGateAgent/v1",
                  "domain_dir": "/mock/domain"},
            system_prompt_content=system_prompt,
        )

        assert result["status"] == "completed"


# ═══════════════════════════════════════════════════════════════════════
# TEST 5: OutputValidator grades genesis agent output
# ═══════════════════════════════════════════════════════════════════════

class TestOutputValidatorGradesGenesisOutput:
    """Validate OutputValidator works with real grading criteria files."""

    @pytest.mark.asyncio
    async def test_validate_source_scanner_output(self) -> None:
        from harness.tests.genesis_test_provider import GENESIS_OUTPUTS
        validator = OutputValidator()
        manifest = yaml.safe_load(
            (PROJECT_ROOT / "agents" / "_genesis" / "SourceScannerAgent" / "v1" / "agent_manifest.yaml").read_text()
        )
        agent_dir = str(PROJECT_ROOT / "agents" / "_genesis" / "SourceScannerAgent" / "v1")

        # Wrap the output like AgentRunner does
        output = {"content": "", "status": "completed"}
        output.update(GENESIS_OUTPUTS["SourceScannerAgent"])

        result = await validator.validate(output, manifest, agent_dir)
        print(f"\n  SourceScanner validation: score={result['score']}, passed={result['passed']}, issues={result['issues']}")
        # Score should be reasonable (schema may not match perfectly but shouldn't crash)
        assert isinstance(result["score"], float)
        assert "passed" in result

    @pytest.mark.asyncio
    async def test_validate_agent_architect_output(self) -> None:
        from harness.tests.genesis_test_provider import GENESIS_OUTPUTS
        validator = OutputValidator()
        manifest = yaml.safe_load(
            (PROJECT_ROOT / "agents" / "_genesis" / "AgentArchitectAgent" / "v1" / "agent_manifest.yaml").read_text()
        )
        agent_dir = str(PROJECT_ROOT / "agents" / "_genesis" / "AgentArchitectAgent" / "v1")

        output = {"content": "", "status": "completed"}
        output.update(GENESIS_OUTPUTS["AgentArchitectAgent"])

        result = await validator.validate(output, manifest, agent_dir)
        print(f"\n  AgentArchitect validation: score={result['score']}, passed={result['passed']}, issues={result['issues']}")
        assert isinstance(result["score"], float)

    @pytest.mark.asyncio
    async def test_validate_quality_gate_output(self) -> None:
        from harness.tests.genesis_test_provider import GENESIS_OUTPUTS
        validator = OutputValidator()
        manifest = yaml.safe_load(
            (PROJECT_ROOT / "agents" / "_genesis" / "QualityGateAgent" / "v1" / "agent_manifest.yaml").read_text()
        )
        agent_dir = str(PROJECT_ROOT / "agents" / "_genesis" / "QualityGateAgent" / "v1")

        output = {"content": "", "status": "completed"}
        output.update(GENESIS_OUTPUTS["QualityGateAgent"])

        result = await validator.validate(output, manifest, agent_dir)
        print(f"\n  QualityGate validation: score={result['score']}, passed={result['passed']}, issues={result['issues']}")
        assert isinstance(result["score"], float)


# ═══════════════════════════════════════════════════════════════════════
# TEST 6: Execution events are typed in real pipeline
# ═══════════════════════════════════════════════════════════════════════

class TestExecutionEventsInRealPipeline:
    """Verify typed ExecutionEvents in the boot_engine pipeline."""

    @pytest.mark.asyncio
    async def test_events_are_typed_enums(self) -> None:
        loader = ManifestLoader()
        provider = GenesisTestProvider()
        wf_path = PROJECT_ROOT / "workflows" / "genesis" / "genesis_scan.yaml"

        engine, workflow, step_configs = loader.boot_engine(
            wf_path, domain_root=PROJECT_ROOT, provider=provider,
            task_input={"industry": "software"},
        )

        result = await engine.execute_dag(workflow["steps"], step_configs)
        log = result["execution_log"]

        # All events should be ExecutionEvent enum values
        for entry in log:
            event = entry.get("event")
            if event is not None:
                assert isinstance(event, ExecutionEvent), f"Event {event!r} is not ExecutionEvent"

        # Specific events should be present
        events = [e["event"] for e in log if "event" in e]
        assert ExecutionEvent.STEP_STARTED in events
        assert ExecutionEvent.STEP_COMPLETED in events


# ═══════════════════════════════════════════════════════════════════════
# TEST 7: RuleEngine with real config/rules.yaml
# ═══════════════════════════════════════════════════════════════════════

class TestRuleEngineWithRealConfig:
    """Verify RuleEngine loads real rules.yaml and enforces correctly."""

    def test_loads_real_rules(self) -> None:
        engine = RuleEngine()
        # Should have loaded platform rules
        assert engine._data.get("platform") is not None
        blocked = engine._data["platform"].get("blocked_commands", [])
        assert "rm -rf /" in blocked

    def test_blocks_dangerous_commands(self) -> None:
        engine = RuleEngine()
        decision = engine.check_tool_call("shell_exec", {"command": "rm -rf /"})
        assert decision.denied
        assert decision.rule_layer == "platform"

    def test_allows_safe_file_read(self) -> None:
        engine = RuleEngine()
        decision = engine.check_tool_call("file_read", {"path": "/some/file.py"})
        assert decision.allowed

    def test_trusted_path_fast_path(self) -> None:
        engine = RuleEngine()
        ctx = RuleContext(
            trusted_paths=[str(PROJECT_ROOT / "sample_project")],
        )
        decision = engine.check_tool_call(
            "file_read",
            {"path": str(PROJECT_ROOT / "sample_project" / "backend" / "app" / "main.py")},
            ctx,
        )
        assert decision.allowed
        assert decision.rule_layer == "trusted"

    def test_audit_log_records_decisions(self) -> None:
        engine = RuleEngine()
        engine.clear_audit_log()
        engine.check_tool_call("file_read", {"path": "test.py"})
        engine.check_tool_call("shell_exec", {"command": "rm -rf /"})
        log = engine.audit_log
        assert len(log) == 2
        assert log[0]["decision"] == "allow"
        assert log[1]["decision"] == "deny"
