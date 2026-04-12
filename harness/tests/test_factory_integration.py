"""Integration tests for the _factory meta-agent domain."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from harness.core.manifest_loader import ManifestLoader
from harness.core.composition_engine import CompositionEngine

PROJECT_ROOT = Path(__file__).parent.parent.parent
FACTORY_AGENTS = PROJECT_ROOT / "agents" / "_factory"
FACTORY_WORKFLOW = PROJECT_ROOT / "workflows" / "factory" / "learn_and_create.yaml"
OPTIMIZE_WORKFLOW = PROJECT_ROOT / "workflows" / "factory" / "optimize.yaml"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class TestFactoryAgentManifests:
    """All _factory agent manifests must be valid and well-formed."""

    @pytest.mark.parametrize("agent_name", [
        "LearnAgent/v1",
        "ContextAgent/v1",
        "AgentFactoryAgent/v1",
        "ValidateTestAgent/v1",
    ])
    def test_manifest_exists_and_valid(self, agent_name: str) -> None:
        manifest_path = FACTORY_AGENTS / agent_name / "agent_manifest.yaml"
        assert manifest_path.exists(), f"Missing: {manifest_path}"
        m = _load_yaml(manifest_path)
        assert m["id"] == f"_factory/{agent_name}"
        assert "execution_mode" in m
        assert "tools" in m
        assert "permissions" in m
        assert "input_schema" in m
        assert "output_schema" in m

    @pytest.mark.parametrize("agent_name", [
        "LearnAgent/v1",
        "ContextAgent/v1",
        "AgentFactoryAgent/v1",
        "ValidateTestAgent/v1",
    ])
    def test_system_prompt_exists(self, agent_name: str) -> None:
        prompt_path = FACTORY_AGENTS / agent_name / "system_prompt.md"
        assert prompt_path.exists(), f"Missing: {prompt_path}"
        content = prompt_path.read_text()
        assert len(content) > 100, "System prompt too short"


class TestFactoryWorkflows:
    def test_learn_and_create_loads(self) -> None:
        assert FACTORY_WORKFLOW.exists()
        wf = _load_yaml(FACTORY_WORKFLOW)
        assert wf["name"] == "learn_and_create"
        assert len(wf["steps"]) == 4
        step_names = [s["name"] for s in wf["steps"]]
        assert step_names == ["learn", "context", "generate", "validate"]

    def test_learn_and_create_dag_valid(self) -> None:
        wf = _load_yaml(FACTORY_WORKFLOW)
        layers = CompositionEngine.topological_sort(wf["steps"])
        assert layers[0] == ["learn"]
        assert "validate" in layers[-1]

    def test_optimize_workflow_loads(self) -> None:
        assert OPTIMIZE_WORKFLOW.exists()
        wf = _load_yaml(OPTIMIZE_WORKFLOW)
        assert wf["name"] == "optimize_domain"

    def test_learn_and_create_has_feedback_loop(self) -> None:
        wf = _load_yaml(FACTORY_WORKFLOW)
        loops = wf.get("feedback_loops", [])
        assert len(loops) >= 1
        assert loops[0]["from_step"] == "validate"
        assert loops[0]["to_step"] == "generate"


class TestFactoryManifestLoader:
    """ManifestLoader should be able to load factory agent manifests."""

    def test_load_learn_agent(self) -> None:
        loader = ManifestLoader()
        agent_dir = FACTORY_AGENTS / "LearnAgent" / "v1"
        manifest, prompt, context = loader.load_agent(agent_dir)
        assert manifest["id"] == "_factory/LearnAgent/v1"
        assert "LearnAgent" in prompt
        assert manifest["execution_mode"]["primary"] == "plan_execute"

    def test_load_agent_factory_agent(self) -> None:
        loader = ManifestLoader()
        agent_dir = FACTORY_AGENTS / "AgentFactoryAgent" / "v1"
        manifest, prompt, context = loader.load_agent(agent_dir)
        assert manifest["id"] == "_factory/AgentFactoryAgent/v1"
        assert "file_write" in str(manifest["tools"])


class TestFactoryStubExecution:
    """Run factory workflow with stub runner to verify pipeline."""

    @pytest.mark.asyncio
    async def test_learn_and_create_stub_run(self) -> None:
        class StubRunner:
            async def run(
                self,
                manifest: Any = None,
                task: Any = None,
                system_prompt_content: str = "",
                context_items: list | None = None,
            ) -> dict:
                agent_id = (manifest or {}).get("id", "unknown")
                return {"status": "completed", "output": f"[STUB] {agent_id} done"}

        wf = _load_yaml(FACTORY_WORKFLOW)
        loader = ManifestLoader()

        engine = CompositionEngine(agent_runner=StubRunner())

        # Build minimal step_configs
        step_configs: dict[str, dict[str, Any]] = {}
        for step in wf["steps"]:
            step_name = step["name"]
            agent_id = step["agent"]
            parts = agent_id.split("/")
            if len(parts) >= 3:
                agent_dir = FACTORY_AGENTS / parts[1] / parts[2]
            else:
                agent_dir = FACTORY_AGENTS
            manifest_path = agent_dir / "agent_manifest.yaml"
            manifest = _load_yaml(manifest_path) if manifest_path.exists() else {"id": agent_id}
            step_configs[step_name] = {
                "manifest": manifest,
                "task": {"repo_path": "/tmp/test"},
                "system_prompt": "",
                "context_items": [],
            }

        result = await engine.execute_dag(wf["steps"], step_configs)
        assert result["status"] == "completed"
        assert set(result["step_results"].keys()) == {"learn", "context", "generate", "validate"}
