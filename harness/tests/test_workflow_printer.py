"""Tests for S4 — stage-grouped workflow printer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from harness.cli.workflow_printer import (
    format_grouped_workflow,
    group_steps_by_stage,
)


def _write_agent(tmp_path: Path, agent_id: str, stage: str | None = None) -> None:
    parts = agent_id.split("/")
    agent_dir = tmp_path / "agents" / "/".join(parts)
    agent_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "id": agent_id,
        "domain": parts[0],
        "category": "reasoning",
        "system_prompt_ref": "system_prompt.md",
    }
    if stage is not None:
        manifest["stage"] = stage
    (agent_dir / "agent_manifest.yaml").write_text(yaml.dump(manifest), encoding="utf-8")


class TestGrouping:
    def test_tagged_manifests_group_by_stage(self, tmp_path: Path) -> None:
        _write_agent(tmp_path, "d/Scanner/v1", stage="analyze")
        _write_agent(tmp_path, "d/Writer/v1", stage="generate")
        _write_agent(tmp_path, "d/Reviewer/v1", stage="review")

        workflow = {
            "name": "demo",
            "steps": [
                {"name": "scan", "agent": "d/Scanner/v1"},
                {"name": "write", "agent": "d/Writer/v1"},
                {"name": "review", "agent": "d/Reviewer/v1"},
            ],
        }
        grouped = group_steps_by_stage(workflow, project_root=tmp_path)
        assert list(grouped.keys()) == ["analyze", "generate", "review"]
        assert grouped["analyze"][0]["name"] == "scan"
        assert grouped["generate"][0]["name"] == "write"
        assert grouped["review"][0]["name"] == "review"

    def test_untagged_agents_go_to_fallback_bucket(self, tmp_path: Path) -> None:
        _write_agent(tmp_path, "d/Tagged/v1", stage="analyze")
        _write_agent(tmp_path, "d/Untagged/v1", stage=None)

        workflow = {
            "name": "mixed",
            "steps": [
                {"name": "a", "agent": "d/Tagged/v1"},
                {"name": "b", "agent": "d/Untagged/v1"},
            ],
        }
        grouped = group_steps_by_stage(workflow, project_root=tmp_path)
        assert "analyze" in grouped
        assert "untagged" in grouped
        # Untagged bucket always lands last
        assert list(grouped.keys())[-1] == "untagged"

    def test_stable_step_order_within_bucket(self, tmp_path: Path) -> None:
        _write_agent(tmp_path, "d/A/v1", stage="analyze")
        _write_agent(tmp_path, "d/B/v1", stage="analyze")
        _write_agent(tmp_path, "d/C/v1", stage="analyze")

        workflow = {
            "name": "x",
            "steps": [
                {"name": "first", "agent": "d/A/v1"},
                {"name": "second", "agent": "d/B/v1"},
                {"name": "third", "agent": "d/C/v1"},
            ],
        }
        grouped = group_steps_by_stage(workflow, project_root=tmp_path)
        names = [s["name"] for s in grouped["analyze"]]
        assert names == ["first", "second", "third"]

    def test_canonical_stage_ordering(self, tmp_path: Path) -> None:
        """Steps emit in Stage enum order, regardless of workflow step order."""
        _write_agent(tmp_path, "d/Sum/v1", stage="summarize")
        _write_agent(tmp_path, "d/Ana/v1", stage="analyze")
        _write_agent(tmp_path, "d/Exe/v1", stage="execute")

        workflow = {
            "name": "reordered",
            "steps": [
                {"name": "s", "agent": "d/Sum/v1"},
                {"name": "e", "agent": "d/Exe/v1"},
                {"name": "a", "agent": "d/Ana/v1"},
            ],
        }
        grouped = group_steps_by_stage(workflow, project_root=tmp_path)
        assert list(grouped.keys()) == ["analyze", "execute", "summarize"]

    def test_unknown_stage_tag_preserved_in_insertion_order(self, tmp_path: Path) -> None:
        _write_agent(tmp_path, "d/Known/v1", stage="analyze")
        _write_agent(tmp_path, "d/Custom/v1", stage="custom-stage")

        workflow = {
            "name": "w",
            "steps": [
                {"name": "c", "agent": "d/Custom/v1"},
                {"name": "a", "agent": "d/Known/v1"},
            ],
        }
        grouped = group_steps_by_stage(workflow, project_root=tmp_path)
        # analyze first (canonical), then custom-stage (unknown)
        keys = list(grouped.keys())
        assert keys[0] == "analyze"
        assert "custom-stage" in keys


class TestLookupBypass:
    """Pre-populated lookup bypasses filesystem reads."""

    def test_lookup_wins_over_disk(self) -> None:
        """Even with no project_root scan, explicit lookup resolves stages."""
        workflow = {
            "name": "cached",
            "steps": [
                {"name": "s1", "agent": "d/Something/v1"},
                {"name": "s2", "agent": "d/Other/v1"},
            ],
        }
        lookup = {"d/Something/v1": "analyze", "d/Other/v1": "review"}
        grouped = group_steps_by_stage(workflow, lookup=lookup)
        assert "analyze" in grouped
        assert "review" in grouped

    def test_lookup_missing_entry_falls_back_to_disk(self, tmp_path: Path) -> None:
        _write_agent(tmp_path, "d/OnDisk/v1", stage="execute")
        workflow = {
            "name": "mixed_lookup",
            "steps": [
                {"name": "c", "agent": "d/Cached/v1"},
                {"name": "d", "agent": "d/OnDisk/v1"},
            ],
        }
        lookup = {"d/Cached/v1": "analyze"}  # OnDisk not in lookup → disk read
        grouped = group_steps_by_stage(workflow, project_root=tmp_path, lookup=lookup)
        assert "analyze" in grouped
        assert "execute" in grouped


class TestFormatting:
    def test_format_includes_workflow_name_and_stage_headers(self, tmp_path: Path) -> None:
        _write_agent(tmp_path, "d/Scanner/v1", stage="analyze")
        _write_agent(tmp_path, "d/Writer/v1", stage="generate")
        workflow = {
            "name": "demo_workflow",
            "steps": [
                {"name": "scan", "agent": "d/Scanner/v1"},
                {"name": "write", "agent": "d/Writer/v1"},
            ],
        }
        out = format_grouped_workflow(workflow, project_root=tmp_path)
        assert "demo_workflow" in out
        assert "[analyze]" in out
        assert "[generate]" in out
        assert "scan" in out
        assert "d/Scanner/v1" in out

    def test_empty_workflow_handled_gracefully(self, tmp_path: Path) -> None:
        out = format_grouped_workflow({"name": "empty", "steps": []}, project_root=tmp_path)
        assert "empty" in out
        assert "(no steps)" in out

    def test_missing_name_uses_placeholder(self, tmp_path: Path) -> None:
        out = format_grouped_workflow({"steps": []}, project_root=tmp_path)
        assert "<unnamed>" in out
