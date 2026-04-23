"""Tests for BestPracticeAdvisorAgent bundle.

The agent itself is LLM-driven (output is markdown + gap_summary +
files array) and validated by genesis e2e runs. Here we pin:

  * Bundle files exist and parse
  * Manifest is single-shot, tools=[], preloads best_practice_library
  * Output schema requires files[] (so Builder's post_execute picks it up)
  * Permissions deny file_edit/file_create — the overlay reaches disk
    via Builder, not this agent directly
"""

from __future__ import annotations

from pathlib import Path

import yaml


_AGENT_DIR = Path(__file__).resolve().parent.parent.parent / "agents" / "_genesis" / "BestPracticeAdvisorAgent" / "v1"


class TestAdvisorBundle:
    def test_manifest_exists(self) -> None:
        assert (_AGENT_DIR / "agent_manifest.yaml").exists()

    def test_system_prompt_exists(self) -> None:
        assert (_AGENT_DIR / "system_prompt.md").exists()

    def test_grading_criteria_exists(self) -> None:
        assert (_AGENT_DIR / "grading_criteria.yaml").exists()

    def test_manifest_single_shot_no_tools(self) -> None:
        m = yaml.safe_load((_AGENT_DIR / "agent_manifest.yaml").read_text(encoding="utf-8"))
        exec_mode = m["execution_mode"]
        assert exec_mode["primary"] == "react"
        assert exec_mode["max_react_steps"] == 1
        assert m["tools"] == []

    def test_manifest_preloads_library(self) -> None:
        m = yaml.safe_load((_AGENT_DIR / "agent_manifest.yaml").read_text(encoding="utf-8"))
        assert "best_practice_library" in m["context"]["preload"]

    def test_output_schema_requires_files_contract(self) -> None:
        """files[] + gap_summary + best_practices_md are the contract."""
        m = yaml.safe_load((_AGENT_DIR / "agent_manifest.yaml").read_text(encoding="utf-8"))
        required = set(m["output_schema"]["required"])
        assert {"best_practices_md", "gap_summary", "files"}.issubset(required)
        gs = m["output_schema"]["properties"]["gap_summary"]["required"]
        assert {"total_principles", "covered", "gaps_must", "gaps_should", "gaps_nice"}.issubset(set(gs))

    def test_output_schema_files_shape_matches_builder(self) -> None:
        """Builder's post_execute writes any {path, content} entry in files[].
        The advisor's schema must match that shape so no wiring is needed."""
        m = yaml.safe_load((_AGENT_DIR / "agent_manifest.yaml").read_text(encoding="utf-8"))
        files_prop = m["output_schema"]["properties"]["files"]
        item_req = files_prop["items"]["required"]
        assert "path" in item_req and "content" in item_req

    def test_permissions_deny_direct_writes(self) -> None:
        """Advisor emits files array; Builder is the only one with file_create rights."""
        m = yaml.safe_load((_AGENT_DIR / "agent_manifest.yaml").read_text(encoding="utf-8"))
        perms = m["permissions"]
        assert perms["file_edit"] == "deny"
        assert perms["file_create"] == "deny"
        assert perms["network_access"] == "deny"

    def test_prompt_forbids_touching_standards(self) -> None:
        """Core non-breaking constraint: the advisor never modifies standards.md."""
        sp = (_AGENT_DIR / "system_prompt.md").read_text(encoding="utf-8")
        assert "Never modify standards.md" in sp or "never modify standards.md" in sp.lower()

    def test_prompt_caps_at_300_lines(self) -> None:
        """Tier 1.5 token budget discipline."""
        sp = (_AGENT_DIR / "system_prompt.md").read_text(encoding="utf-8")
        assert "300 lines" in sp
