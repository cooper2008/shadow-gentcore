"""Tests for genesis_verifier — post-genesis smoke-test of generated domain structure."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.core.genesis_verifier import verify_genesis_output


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_agent(
    agents_dir: Path,
    name: str,
    *,
    manifest_content: str | None = None,
    include_prompt: bool = True,
) -> Path:
    """Create a minimal agent directory under agents_dir."""
    agent_dir = agents_dir / name
    agent_dir.mkdir(parents=True, exist_ok=True)

    if manifest_content is None:
        manifest_content = yaml.dump(
            {"id": name, "domain": "test", "category": "build"},
            default_flow_style=False,
        )

    (agent_dir / "agent_manifest.yaml").write_text(manifest_content, encoding="utf-8")

    if include_prompt:
        (agent_dir / "system_prompt.md").write_text(f"# {name}\nYou are an agent.", encoding="utf-8")

    return agent_dir


def _make_workflow(workflows_dir: Path, name: str = "main.yaml", *, include_steps: bool = True) -> Path:
    workflows_dir.mkdir(parents=True, exist_ok=True)
    content: dict = {"name": name}
    if include_steps:
        content["steps"] = [{"id": "step1", "agent": "agent1"}]
    (workflows_dir / name).write_text(yaml.dump(content), encoding="utf-8")
    return workflows_dir / name


def _make_context(domain_dir: Path, *, include_standards: bool = True) -> Path:
    ctx = domain_dir / "context"
    ctx.mkdir(parents=True, exist_ok=True)
    if include_standards:
        (ctx / "standards.md").write_text("# Standards\nFollow best practices.", encoding="utf-8")
    return ctx


def _make_valid_domain(tmp_path: Path, *, agent_count: int = 1) -> Path:
    """Build a fully valid domain structure."""
    agents_dir = tmp_path / "agents"
    for i in range(agent_count):
        _make_agent(agents_dir, f"agent_{i}")
    _make_workflow(tmp_path / "workflows")
    _make_context(tmp_path)
    return tmp_path


# ── happy path ────────────────────────────────────────────────────────────────


class TestValidDomain:
    def test_single_agent_passes(self, tmp_path: Path) -> None:
        _make_valid_domain(tmp_path)
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is True
        assert result["failure_count"] == 0
        assert result["failures"] == []
        assert result["total_checks"] > 0

    def test_multiple_agents_all_checked(self, tmp_path: Path) -> None:
        _make_valid_domain(tmp_path, agent_count=4)
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is True

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        """verify_genesis_output must accept str as well as Path."""
        _make_valid_domain(tmp_path)
        result = verify_genesis_output(str(tmp_path))
        assert result["passed"] is True

    def test_no_context_dir_still_passes(self, tmp_path: Path) -> None:
        """context/ is optional — its absence does not fail verification."""
        agents_dir = tmp_path / "agents"
        _make_agent(agents_dir, "agent_0")
        _make_workflow(tmp_path / "workflows")
        # deliberately no context/
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is True

    def test_result_keys_always_present(self, tmp_path: Path) -> None:
        _make_valid_domain(tmp_path)
        result = verify_genesis_output(tmp_path)
        for key in ("passed", "total_checks", "failures", "failure_count"):
            assert key in result

    def test_multiple_workflows_all_checked(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _make_agent(agents_dir, "agent_0")
        wf_dir = tmp_path / "workflows"
        _make_workflow(wf_dir, "wf_a.yaml")
        _make_workflow(wf_dir, "wf_b.yaml")
        _make_context(tmp_path)
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is True

    def test_yml_extension_workflow_accepted(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _make_agent(agents_dir, "agent_0")
        wf_dir = tmp_path / "workflows"
        _make_workflow(wf_dir, "main.yml")
        _make_context(tmp_path)
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is True


# ── agents/ failures ──────────────────────────────────────────────────────────


class TestAgentsDirectoryFailures:
    def test_missing_agents_dir(self, tmp_path: Path) -> None:
        _make_workflow(tmp_path / "workflows")
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is False
        assert any("agents/ directory" in f for f in result["failures"])

    def test_empty_agents_dir_no_manifests(self, tmp_path: Path) -> None:
        (tmp_path / "agents").mkdir()
        _make_workflow(tmp_path / "workflows")
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is False
        assert any("No agents found" in f for f in result["failures"])

    def test_agent_invalid_yaml(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _make_agent(agents_dir, "bad_agent", manifest_content=": ][invalid yaml{{", include_prompt=True)
        _make_workflow(tmp_path / "workflows")
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is False
        assert any("invalid YAML" in f for f in result["failures"])

    def test_agent_missing_id_field(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _make_agent(
            agents_dir,
            "no_id",
            manifest_content=yaml.dump({"domain": "test", "category": "build"}),
        )
        _make_workflow(tmp_path / "workflows")
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is False
        assert any("missing 'id'" in f for f in result["failures"])

    def test_agent_missing_domain_field(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _make_agent(
            agents_dir,
            "no_domain",
            manifest_content=yaml.dump({"id": "no_domain", "category": "build"}),
        )
        _make_workflow(tmp_path / "workflows")
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is False
        assert any("missing 'domain'" in f for f in result["failures"])

    def test_agent_missing_category_field(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _make_agent(
            agents_dir,
            "no_category",
            manifest_content=yaml.dump({"id": "no_category", "domain": "test"}),
        )
        _make_workflow(tmp_path / "workflows")
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is False
        assert any("missing 'category'" in f for f in result["failures"])

    def test_agent_missing_system_prompt(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _make_agent(agents_dir, "no_prompt", include_prompt=False)
        _make_workflow(tmp_path / "workflows")
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is False
        assert any("missing system_prompt.md" in f for f in result["failures"])

    def test_agent_custom_prompt_ref_missing(self, tmp_path: Path) -> None:
        """system_prompt_ref pointing to a non-existent file triggers failure."""
        agents_dir = tmp_path / "agents"
        manifest = yaml.dump(
            {"id": "custom", "domain": "test", "category": "build", "system_prompt_ref": "custom_prompt.md"}
        )
        _make_agent(agents_dir, "custom_agent", manifest_content=manifest, include_prompt=False)
        _make_workflow(tmp_path / "workflows")
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is False
        assert any("custom_prompt.md" in f for f in result["failures"])

    def test_multiple_agents_one_broken_reports_all_issues(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _make_agent(agents_dir, "good_agent")
        _make_agent(
            agents_dir,
            "bad_agent",
            manifest_content=yaml.dump({"id": "bad_agent"}),  # missing domain + category
            include_prompt=False,
        )
        _make_workflow(tmp_path / "workflows")
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is False
        # Should report domain, category, and missing system_prompt
        assert result["failure_count"] >= 3


# ── workflows/ failures ───────────────────────────────────────────────────────


class TestWorkflowsFailures:
    def test_missing_workflows_dir(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _make_agent(agents_dir, "agent_0")
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is False
        assert any("workflows/" in f for f in result["failures"])

    def test_empty_workflows_dir(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _make_agent(agents_dir, "agent_0")
        (tmp_path / "workflows").mkdir()
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is False
        assert any("No workflows" in f for f in result["failures"])

    def test_workflow_missing_steps(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _make_agent(agents_dir, "agent_0")
        _make_workflow(tmp_path / "workflows", include_steps=False)
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is False
        assert any("missing 'steps'" in f for f in result["failures"])

    def test_workflow_invalid_yaml(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _make_agent(agents_dir, "agent_0")
        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir(parents=True, exist_ok=True)
        (wf_dir / "broken.yaml").write_text(": ][{{", encoding="utf-8")
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is False
        assert any("invalid YAML" in f for f in result["failures"])


# ── context/ failures ─────────────────────────────────────────────────────────


class TestContextFailures:
    def test_context_dir_exists_but_no_standards(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents"
        _make_agent(agents_dir, "agent_0")
        _make_workflow(tmp_path / "workflows")
        _make_context(tmp_path, include_standards=False)
        result = verify_genesis_output(tmp_path)
        assert result["passed"] is False
        assert any("standards.md" in f for f in result["failures"])


# ── total_checks counter ──────────────────────────────────────────────────────


class TestCheckCounting:
    def test_total_checks_increases_with_more_agents(self, tmp_path: Path) -> None:
        def _count(n: int) -> int:
            d = tmp_path / f"domain_{n}"
            d.mkdir()
            _make_valid_domain(d, agent_count=n)
            return verify_genesis_output(d)["total_checks"]

        assert _count(1) < _count(3)

    def test_total_checks_increases_with_more_workflows(self, tmp_path: Path) -> None:
        def _count(n: int) -> int:
            d = tmp_path / f"wf_domain_{n}"
            d.mkdir()
            agents_dir = d / "agents"
            _make_agent(agents_dir, "agent_0")
            wf_dir = d / "workflows"
            for i in range(n):
                _make_workflow(wf_dir, f"wf_{i}.yaml")
            _make_context(d)
            return verify_genesis_output(d)["total_checks"]

        assert _count(1) < _count(3)
