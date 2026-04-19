"""Tests for B1 — 6 new generic non-SWE stage agents (fix/B1-generic-stages).

Asserts that each agent dir exists under `_shared/`, has a parseable manifest,
is tagged with the correct Stage, declares at least one tool bound to a tool
pack, and has a system prompt that references `context/standards.md` (so
behaviour is context-driven, matching the thesis).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_contracts.manifests.agent_manifest import AgentManifest
from agent_contracts.stages import Stage


SHARED_DIR = Path(__file__).resolve().parent.parent.parent / "agents" / "_shared"

# (agent_name, expected_stage)
B1_AGENTS: list[tuple[str, Stage]] = [
    ("TriageAgent", Stage.ANALYZE),
    ("InvestigateAgent", Stage.ANALYZE),
    ("ExecuteAgent", Stage.EXECUTE),
    ("RespondAgent", Stage.RESPOND),
    ("SummarizeAgent", Stage.SUMMARIZE),
    ("RetrieveAgent", Stage.RETRIEVE),
]


def _load_manifest(agent_name: str) -> AgentManifest:
    path = SHARED_DIR / agent_name / "v1" / "agent_manifest.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return AgentManifest(**data)


class TestB1AgentDirectoriesExist:
    @pytest.mark.parametrize("agent_name,_", B1_AGENTS)
    def test_directory_present(self, agent_name: str, _: Stage) -> None:
        agent_dir = SHARED_DIR / agent_name / "v1"
        assert agent_dir.is_dir(), f"Missing agent directory: {agent_dir}"

    @pytest.mark.parametrize("agent_name,_", B1_AGENTS)
    def test_manifest_file_present(self, agent_name: str, _: Stage) -> None:
        manifest = SHARED_DIR / agent_name / "v1" / "agent_manifest.yaml"
        assert manifest.is_file(), f"Missing manifest: {manifest}"

    @pytest.mark.parametrize("agent_name,_", B1_AGENTS)
    def test_system_prompt_present(self, agent_name: str, _: Stage) -> None:
        prompt = SHARED_DIR / agent_name / "v1" / "system_prompt.md"
        assert prompt.is_file(), f"Missing system prompt: {prompt}"


class TestB1ManifestSchema:
    @pytest.mark.parametrize("agent_name,_", B1_AGENTS)
    def test_manifest_parses(self, agent_name: str, _: Stage) -> None:
        m = _load_manifest(agent_name)
        assert m.id == f"_shared/{agent_name}/v1"
        assert m.domain == "_shared"

    @pytest.mark.parametrize("agent_name,expected_stage", B1_AGENTS)
    def test_stage_correctly_tagged(self, agent_name: str, expected_stage: Stage) -> None:
        m = _load_manifest(agent_name)
        assert m.stage is expected_stage, (
            f"{agent_name} should be stage={expected_stage.value}, got stage={m.stage!r}"
        )

    @pytest.mark.parametrize("agent_name,_", B1_AGENTS)
    def test_has_description(self, agent_name: str, _: Stage) -> None:
        data = yaml.safe_load(
            (SHARED_DIR / agent_name / "v1" / "agent_manifest.yaml").read_text(encoding="utf-8")
        )
        # `description` is not in AgentManifest schema but is required by convention
        assert data.get("description"), f"{agent_name} has no top-level description"
        assert len(data["description"]) >= 20

    @pytest.mark.parametrize("agent_name,_", B1_AGENTS)
    def test_has_at_least_one_tool_with_pack(self, agent_name: str, _: Stage) -> None:
        m = _load_manifest(agent_name)
        assert len(m.tools) >= 1, f"{agent_name} has no tools"
        for tool in m.tools:
            assert tool.name, f"{agent_name} has a tool without name"
            assert tool.pack, f"{agent_name} has a tool without pack URI"

    @pytest.mark.parametrize("agent_name,_", B1_AGENTS)
    def test_has_input_and_output_schemas(self, agent_name: str, _: Stage) -> None:
        data = yaml.safe_load(
            (SHARED_DIR / agent_name / "v1" / "agent_manifest.yaml").read_text(encoding="utf-8")
        )
        assert "input_schema" in data, f"{agent_name} missing input_schema"
        assert "output_schema" in data, f"{agent_name} missing output_schema"


class TestB1SystemPromptsAreThin:
    @pytest.mark.parametrize("agent_name,_", B1_AGENTS)
    def test_prompt_has_real_content(self, agent_name: str, _: Stage) -> None:
        prompt = (SHARED_DIR / agent_name / "v1" / "system_prompt.md").read_text(encoding="utf-8")
        assert len(prompt) > 200, f"{agent_name} prompt is too short to be useful"

    @pytest.mark.parametrize("agent_name,_", B1_AGENTS)
    def test_prompt_references_standards_md(self, agent_name: str, _: Stage) -> None:
        """Thin-agent thesis: behaviour shaped by context, not baked-in prompts."""
        prompt = (SHARED_DIR / agent_name / "v1" / "system_prompt.md").read_text(encoding="utf-8")
        assert "standards.md" in prompt, (
            f"{agent_name} prompt must reference context/standards.md for domain-driven behaviour"
        )


class TestB1CoverageOfStageGaps:
    def test_six_agents_cover_non_swe_stage_gaps(self) -> None:
        """The six B1 agents together must cover the gaps the audit identified."""
        stages_present = {stage for _, stage in B1_AGENTS}
        # The gap: existing _shared had no agent for these stages
        assert Stage.RESPOND in stages_present
        assert Stage.SUMMARIZE in stages_present
        assert Stage.RETRIEVE in stages_present
        # Analyze/Execute are enriched — not previously covered for non-SWE domains
        assert Stage.ANALYZE in stages_present
        assert Stage.EXECUTE in stages_present

    def test_triage_and_investigate_are_distinct(self) -> None:
        """Both are analyze-stage but must have different execution modes."""
        triage = _load_manifest("TriageAgent")
        investigate = _load_manifest("InvestigateAgent")
        # Triage is fast classification; Investigate is deep exploration
        assert triage.execution_mode.primary != investigate.execution_mode.primary
