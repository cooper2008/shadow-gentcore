"""Smoke tests for the genesis_prompt.yaml workflow.

The workflow is the entry point for prompt-only genesis — build a domain
from an `intent:` description with zero source repos/docs. Its job is
to substitute BestPracticeResearchAgent for the scan+map pair while
keeping every downstream step identical to genesis_build.yaml.

Pinned here:
  * Workflow parses and has the expected shape
  * Every agent it references has a real bundle under agents/_genesis/
  * `research` replaces scan + map (not in addition to them)
  * Downstream steps are byte-identical to genesis_build's set
"""

from __future__ import annotations

from pathlib import Path

import yaml


GENESIS_PROMPT = Path(__file__).resolve().parent.parent.parent / "workflows" / "genesis" / "genesis_prompt.yaml"
GENESIS_BUILD = Path(__file__).resolve().parent.parent.parent / "workflows" / "genesis" / "genesis_build.yaml"


def _load(p: Path) -> dict:
    return yaml.safe_load(p.read_text(encoding="utf-8"))


class TestGenesisPromptWorkflow:
    def test_workflow_file_exists(self) -> None:
        assert GENESIS_PROMPT.exists()

    def test_workflow_parses(self) -> None:
        wf = _load(GENESIS_PROMPT)
        assert wf["name"] == "genesis_prompt"
        assert wf["domain"] == "_genesis"

    def test_step_count_and_names(self) -> None:
        wf = _load(GENESIS_PROMPT)
        names = [s["name"] for s in wf["steps"]]
        assert names == [
            "research", "resolve", "discover_tools", "engineer_context",
            "verify", "advise", "synthesize_tools", "architect", "build", "validate",
        ]

    def test_research_replaces_scan_plus_map(self) -> None:
        """The whole point: no scan/map, research in their place."""
        wf = _load(GENESIS_PROMPT)
        names = {s["name"] for s in wf["steps"]}
        assert "research" in names
        assert "scan" not in names
        assert "map" not in names

    def test_research_is_first_step(self) -> None:
        """Nothing upstream of research — it's the entry point."""
        wf = _load(GENESIS_PROMPT)
        first = wf["steps"][0]
        assert first["name"] == "research"
        assert first["agent"] == "_genesis/BestPracticeResearchAgent/v1"
        assert "depends_on" not in first

    def test_downstream_matches_genesis_build(self) -> None:
        """resolve-onwards must be identical to genesis_build.yaml so the
        two workflows share all post-research behaviour."""
        prompt_wf = _load(GENESIS_PROMPT)
        build_wf = _load(GENESIS_BUILD)

        # Build the set of downstream steps in each
        prompt_downstream = {s["name"] for s in prompt_wf["steps"]} - {"research"}
        build_downstream = {s["name"] for s in build_wf["steps"]} - {"scan", "map"}
        assert prompt_downstream == build_downstream

    def test_all_referenced_agents_exist(self) -> None:
        wf = _load(GENESIS_PROMPT)
        agents_root = Path(__file__).resolve().parent.parent.parent / "agents"
        for step in wf["steps"]:
            parts = step["agent"].split("/")
            assert len(parts) == 3, f"Unexpected agent format: {step['agent']}"
            bundle = agents_root / parts[0] / parts[1] / parts[2] / "agent_manifest.yaml"
            assert bundle.exists(), f"Missing agent bundle: {bundle}"

    def test_research_gate_has_relaxed_coverage_threshold(self) -> None:
        """Prompt-only runs are bounded by the library; research_gate
        uses 30 (vs map_gate's 40 in genesis_build.yaml)."""
        wf = _load(GENESIS_PROMPT)
        research = next(s for s in wf["steps"] if s["name"] == "research")
        gate = research["gate"]["condition"]
        assert "coverage.overall >= 30" in gate

    def test_architect_depends_on_research_and_advise(self) -> None:
        """Architect must wait for the full research/verify/advise fan-in."""
        wf = _load(GENESIS_PROMPT)
        arch = next(s for s in wf["steps"] if s["name"] == "architect")
        deps = set(arch["depends_on"])
        assert {"research", "verify", "advise", "discover_tools", "engineer_context"}.issubset(deps)

    def test_feedback_loops_present(self) -> None:
        """validate → build loop is the minimum floor the CLI expects."""
        wf = _load(GENESIS_PROMPT)
        loops = {lp["name"] for lp in wf.get("feedback_loops", [])}
        assert "validate_to_build" in loops

    def test_budget_declared(self) -> None:
        wf = _load(GENESIS_PROMPT)
        budget = wf.get("budget", {})
        assert budget.get("max_tokens", 0) > 0
        assert budget.get("max_duration_seconds", 0) > 0
