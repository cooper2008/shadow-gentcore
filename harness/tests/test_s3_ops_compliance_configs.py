"""Tests for S3 (shadow-gentcore side) — categories.yaml + rules.yaml + agent retag.

Companion to the agent-contracts S3 commit. Adds:
- `ops` and `compliance` entries in config/categories.yaml
- `category_overrides` for both in config/rules.yaml
- Re-tags 8 agents that previously sat in `reasoning` / `security-analysis` but
  need shell_command: allow (would be silently blocked under H3).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CATEGORIES_YAML = REPO_ROOT / "config" / "categories.yaml"
RULES_YAML = REPO_ROOT / "config" / "rules.yaml"
SHARED_DIR = REPO_ROOT / "agents" / "_shared"

# Agents that must move to `ops` (shell-executing execution-stage).
OPS_AGENTS = [
    "DeployAgent",
    "RollbackAgent",
    "TestRunnerAgent",
    "LinterAgent",
    "IntegrationTestAgent",
    "PerformanceTestAgent",
    "DependencyAnalyzerAgent",
    "SecurityScanAgent",
]

# Agents that must move to `compliance` (distinct from security-analysis).
COMPLIANCE_AGENTS = [
    "ComplianceCheckerAgent",
]


def _load_manifest(agent_name: str) -> dict:
    path = SHARED_DIR / agent_name / "v1" / "agent_manifest.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class TestCategoriesYamlContainsNewCategories:
    def setup_method(self) -> None:
        self.data = yaml.safe_load(CATEGORIES_YAML.read_text(encoding="utf-8"))

    def test_ops_defined(self) -> None:
        assert "ops" in self.data["categories"]
        ops = self.data["categories"]["ops"]
        assert "provider" in ops
        assert "model" in ops

    def test_compliance_defined(self) -> None:
        assert "compliance" in self.data["categories"]
        comp = self.data["categories"]["compliance"]
        assert "provider" in comp
        assert "model" in comp

    def test_ops_temperature_is_low(self) -> None:
        assert self.data["categories"]["ops"]["temperature"] <= 0.3

    def test_compliance_temperature_is_low(self) -> None:
        assert self.data["categories"]["compliance"]["temperature"] <= 0.3


class TestRulesYamlContainsCategoryOverrides:
    def setup_method(self) -> None:
        self.data = yaml.safe_load(RULES_YAML.read_text(encoding="utf-8"))
        self.overrides = self.data.get("category_overrides", {})

    def test_ops_category_override_defined(self) -> None:
        assert "ops" in self.overrides

    def test_ops_allows_shell_command(self) -> None:
        assert self.overrides["ops"]["shell_command"] == "allow"

    def test_ops_denies_file_edit(self) -> None:
        """Ops executes, doesn't mutate files directly — file_edit stays deny."""
        assert self.overrides["ops"]["file_edit"] == "deny"

    def test_compliance_category_override_defined(self) -> None:
        assert "compliance" in self.overrides

    def test_compliance_denies_all_writes(self) -> None:
        overrides = self.overrides["compliance"]
        assert overrides.get("file_edit") == "deny"
        assert overrides.get("file_create") == "deny"
        assert overrides.get("shell_command") == "deny"


class TestAgentsMovedToOps:
    @pytest.mark.parametrize("agent_name", OPS_AGENTS)
    def test_agent_category_is_ops(self, agent_name: str) -> None:
        manifest = _load_manifest(agent_name)
        assert manifest.get("category") == "ops", (
            f"{agent_name} must be category=ops post-S3 so shell_command: allow "
            f"survives the permission-engine merge once H3 lands"
        )

    @pytest.mark.parametrize("agent_name", OPS_AGENTS)
    def test_agent_still_allows_shell_command(self, agent_name: str) -> None:
        manifest = _load_manifest(agent_name)
        assert manifest["permissions"]["shell_command"] == "allow"


class TestAgentsMovedToCompliance:
    @pytest.mark.parametrize("agent_name", COMPLIANCE_AGENTS)
    def test_agent_category_is_compliance(self, agent_name: str) -> None:
        manifest = _load_manifest(agent_name)
        assert manifest.get("category") == "compliance"

    @pytest.mark.parametrize("agent_name", COMPLIANCE_AGENTS)
    def test_agent_remains_read_only(self, agent_name: str) -> None:
        manifest = _load_manifest(agent_name)
        assert manifest["permissions"]["file_edit"] == "deny"
        assert manifest["permissions"]["shell_command"] == "deny"


class TestH3MergePreview:
    """Simulate the Layer 2 + Layer 4 merge that will fire when H3 lands.

    For each re-categorised agent, verify that the merge of:
      - category_override (from config/rules.yaml)
      - agent permissions (from manifest)
    yields the same values as before, i.e. no surprise denial when the
    permission context is actually wired in.
    """

    def _merge(self, cat_override: dict, agent_perms: dict, action: str) -> str:
        order = {"deny": 0, "ask": 1, "allow": 2}
        values = []
        if action in cat_override:
            values.append(cat_override[action])
        if action in agent_perms:
            values.append(agent_perms[action])
        if not values:
            return "ask"
        result = values[0]
        for v in values[1:]:
            if order[v] < order[result]:
                result = v
        return result

    @pytest.mark.parametrize("agent_name", OPS_AGENTS)
    def test_ops_agents_can_still_run_shell_after_merge(self, agent_name: str) -> None:
        rules = yaml.safe_load(RULES_YAML.read_text(encoding="utf-8"))
        ops_override = rules["category_overrides"]["ops"]
        manifest = _load_manifest(agent_name)
        merged = self._merge(ops_override, manifest["permissions"], "shell_command")
        assert merged == "allow", (
            f"H3 merge preview: {agent_name} would be blocked from shell_command — "
            f"ops_override={ops_override.get('shell_command')}, "
            f"manifest={manifest['permissions'].get('shell_command')}"
        )

    @pytest.mark.parametrize("agent_name", COMPLIANCE_AGENTS)
    def test_compliance_agents_stay_denied_writes_after_merge(self, agent_name: str) -> None:
        rules = yaml.safe_load(RULES_YAML.read_text(encoding="utf-8"))
        comp_override = rules["category_overrides"]["compliance"]
        manifest = _load_manifest(agent_name)
        merged = self._merge(comp_override, manifest["permissions"], "file_edit")
        assert merged == "deny"
