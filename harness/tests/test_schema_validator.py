"""Tests for harness.core.schema_validator — agent and workflow YAML validation."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.core.schema_validator import validate_agent, validate_workflow


# ── helpers ───────────────────────────────────────────────────────────────────


def _write(path: Path, data: object) -> None:
    """Dump *data* as YAML to *path*, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")


def _make_agent(
    tmp_path: Path,
    *,
    manifest: dict | None = None,
    with_prompt: bool = True,
    raw_yaml: str | None = None,
) -> Path:
    """Scaffold a minimal valid agent bundle under *tmp_path*."""
    agent_dir = tmp_path / "MyAgent" / "v1"
    agent_dir.mkdir(parents=True, exist_ok=True)

    if raw_yaml is not None:
        (agent_dir / "agent_manifest.yaml").write_text(raw_yaml, encoding="utf-8")
    else:
        data = manifest if manifest is not None else {
            "id": "test/MyAgent/v1",
            "domain": "test",
            "category": "reasoning",
            "description": "A test agent",
            "system_prompt_ref": "system_prompt.md",
        }
        _write(agent_dir / "agent_manifest.yaml", data)

    if with_prompt:
        (agent_dir / "system_prompt.md").write_text("You are a test agent.", encoding="utf-8")

    return agent_dir


def _make_workflow(tmp_path: Path, data: dict | None = None, *, raw_yaml: str | None = None) -> Path:
    """Write a workflow YAML to *tmp_path*/workflow.yaml."""
    wf = tmp_path / "workflow.yaml"
    if raw_yaml is not None:
        wf.write_text(raw_yaml, encoding="utf-8")
    else:
        content = data if data is not None else {
            "name": "test_workflow",
            "steps": [
                {"name": "step_a", "agent": "test/AgentA/v1"},
                {"name": "step_b", "agent": "test/AgentB/v1", "depends_on": ["step_a"]},
            ],
        }
        _write(wf, content)
    return wf


# ── validate_agent ─────────────────────────────────────────────────────────────


class TestValidateAgent:

    def test_valid_agent_returns_no_issues(self, tmp_path: Path) -> None:
        agent_dir = _make_agent(tmp_path)
        assert validate_agent(agent_dir) == []

    def test_missing_directory(self, tmp_path: Path) -> None:
        issues = validate_agent(tmp_path / "nonexistent")
        assert len(issues) == 1
        assert "not found" in issues[0]

    def test_missing_manifest(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "AgentX" / "v1"
        agent_dir.mkdir(parents=True)
        issues = validate_agent(agent_dir)
        assert any("agent_manifest.yaml" in i for i in issues)

    def test_invalid_yaml_in_manifest(self, tmp_path: Path) -> None:
        agent_dir = _make_agent(tmp_path, raw_yaml=": : invalid: yaml: [\n")
        issues = validate_agent(agent_dir)
        assert any("Invalid YAML" in i for i in issues)

    def test_missing_required_field_id(self, tmp_path: Path) -> None:
        m = {"domain": "test", "category": "reasoning", "description": "x"}
        agent_dir = _make_agent(tmp_path, manifest=m)
        issues = validate_agent(agent_dir)
        assert any("id" in i for i in issues)

    def test_missing_required_field_domain(self, tmp_path: Path) -> None:
        m = {"id": "test/A/v1", "category": "reasoning", "description": "x"}
        agent_dir = _make_agent(tmp_path, manifest=m)
        issues = validate_agent(agent_dir)
        assert any("domain" in i for i in issues)

    def test_missing_required_field_category(self, tmp_path: Path) -> None:
        m = {"id": "test/A/v1", "domain": "test", "description": "x"}
        agent_dir = _make_agent(tmp_path, manifest=m)
        issues = validate_agent(agent_dir)
        assert any("category" in i for i in issues)

    def test_missing_required_field_description(self, tmp_path: Path) -> None:
        m = {"id": "test/A/v1", "domain": "test", "category": "reasoning"}
        agent_dir = _make_agent(tmp_path, manifest=m)
        issues = validate_agent(agent_dir)
        assert any("description" in i for i in issues)

    def test_all_required_fields_missing_reported_separately(self, tmp_path: Path) -> None:
        agent_dir = _make_agent(tmp_path, manifest={})
        issues = validate_agent(agent_dir)
        required = {"id", "domain", "category", "description"}
        for field in required:
            assert any(field in i for i in issues), f"Expected issue for missing field '{field}'"

    def test_missing_system_prompt(self, tmp_path: Path) -> None:
        agent_dir = _make_agent(tmp_path, with_prompt=False)
        issues = validate_agent(agent_dir)
        assert any("system_prompt" in i.lower() for i in issues)

    def test_custom_system_prompt_ref_missing(self, tmp_path: Path) -> None:
        m = {
            "id": "test/A/v1", "domain": "test", "category": "reasoning",
            "description": "x", "system_prompt_ref": "custom_prompt.md",
        }
        agent_dir = _make_agent(tmp_path, manifest=m, with_prompt=False)
        issues = validate_agent(agent_dir)
        assert any("custom_prompt.md" in i for i in issues)

    def test_hooks_ref_missing(self, tmp_path: Path) -> None:
        m = {
            "id": "test/A/v1", "domain": "test", "category": "reasoning",
            "description": "x", "hooks_ref": "hooks.py",
        }
        agent_dir = _make_agent(tmp_path, manifest=m)
        issues = validate_agent(agent_dir)
        assert any("hooks.py" in i for i in issues)

    def test_hooks_ref_present_no_issue(self, tmp_path: Path) -> None:
        m = {
            "id": "test/A/v1", "domain": "test", "category": "reasoning",
            "description": "x", "hooks_ref": "hooks.py",
        }
        agent_dir = _make_agent(tmp_path, manifest=m)
        (agent_dir / "hooks.py").write_text("# hooks", encoding="utf-8")
        issues = validate_agent(agent_dir)
        assert not any("hooks.py" in i for i in issues)

    def test_grading_criteria_ref_missing(self, tmp_path: Path) -> None:
        m = {
            "id": "test/A/v1", "domain": "test", "category": "reasoning",
            "description": "x", "grading_criteria_ref": "grading.yaml",
        }
        agent_dir = _make_agent(tmp_path, manifest=m)
        issues = validate_agent(agent_dir)
        assert any("grading.yaml" in i for i in issues)

    def test_grading_criteria_ref_present_no_issue(self, tmp_path: Path) -> None:
        m = {
            "id": "test/A/v1", "domain": "test", "category": "reasoning",
            "description": "x", "grading_criteria_ref": "grading.yaml",
        }
        agent_dir = _make_agent(tmp_path, manifest=m)
        _write(agent_dir / "grading.yaml", {"criteria": []})
        issues = validate_agent(agent_dir)
        assert not any("grading.yaml" in i for i in issues)

    def test_tools_not_a_list(self, tmp_path: Path) -> None:
        m = {
            "id": "test/A/v1", "domain": "test", "category": "reasoning",
            "description": "x", "tools": "not-a-list",
        }
        agent_dir = _make_agent(tmp_path, manifest=m)
        issues = validate_agent(agent_dir)
        assert any("tools" in i for i in issues)

    def test_tools_as_list_no_issue(self, tmp_path: Path) -> None:
        m = {
            "id": "test/A/v1", "domain": "test", "category": "reasoning",
            "description": "x", "tools": [{"name": "file_read"}],
        }
        agent_dir = _make_agent(tmp_path, manifest=m)
        issues = validate_agent(agent_dir)
        assert not any("tools" in i for i in issues)

    @pytest.mark.parametrize("mode", ["react", "chain_of_thought", "plan_execute", "direct"])
    def test_valid_execution_modes_string(self, tmp_path: Path, mode: str) -> None:
        m = {
            "id": "test/A/v1", "domain": "test", "category": "reasoning",
            "description": "x", "execution_mode": mode,
        }
        agent_dir = _make_agent(tmp_path, manifest=m)
        issues = validate_agent(agent_dir)
        assert not any("execution_mode" in i for i in issues)

    def test_valid_execution_mode_mapping(self, tmp_path: Path) -> None:
        m = {
            "id": "test/A/v1", "domain": "test", "category": "reasoning",
            "description": "x",
            "execution_mode": {"primary": "plan_execute", "fallback": "react", "max_react_steps": 20},
        }
        agent_dir = _make_agent(tmp_path, manifest=m)
        issues = validate_agent(agent_dir)
        assert not any("execution_mode" in i for i in issues)

    def test_unknown_execution_mode(self, tmp_path: Path) -> None:
        m = {
            "id": "test/A/v1", "domain": "test", "category": "reasoning",
            "description": "x", "execution_mode": "turbo_autopilot",
        }
        agent_dir = _make_agent(tmp_path, manifest=m)
        issues = validate_agent(agent_dir)
        assert any("turbo_autopilot" in i for i in issues)

    def test_unknown_execution_mode_in_mapping(self, tmp_path: Path) -> None:
        m = {
            "id": "test/A/v1", "domain": "test", "category": "reasoning",
            "description": "x",
            "execution_mode": {"primary": "warp_speed"},
        }
        agent_dir = _make_agent(tmp_path, manifest=m)
        issues = validate_agent(agent_dir)
        assert any("warp_speed" in i for i in issues)

    def test_manifest_root_not_dict(self, tmp_path: Path) -> None:
        agent_dir = _make_agent(tmp_path, raw_yaml="- item1\n- item2\n")
        issues = validate_agent(agent_dir)
        assert any("mapping" in i for i in issues)


# ── validate_workflow ──────────────────────────────────────────────────────────


class TestValidateWorkflow:

    def test_valid_workflow_returns_no_issues(self, tmp_path: Path) -> None:
        wf = _make_workflow(tmp_path)
        assert validate_workflow(wf) == []

    def test_file_not_found(self, tmp_path: Path) -> None:
        issues = validate_workflow(tmp_path / "missing.yaml")
        assert any("not found" in i for i in issues)

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        wf = tmp_path / "bad.yaml"
        wf.write_text(": : [\n", encoding="utf-8")
        issues = validate_workflow(wf)
        assert any("Invalid YAML" in i for i in issues)

    def test_missing_required_field_name(self, tmp_path: Path) -> None:
        data = {"steps": [{"name": "a", "agent": "x/A/v1"}]}
        wf = _make_workflow(tmp_path, data)
        issues = validate_workflow(wf)
        assert any("name" in i for i in issues)

    def test_missing_required_field_steps(self, tmp_path: Path) -> None:
        data = {"name": "my_workflow"}
        wf = _make_workflow(tmp_path, data)
        issues = validate_workflow(wf)
        assert any("steps" in i for i in issues)

    def test_step_missing_name(self, tmp_path: Path) -> None:
        data = {
            "name": "wf",
            "steps": [{"agent": "test/A/v1"}],
        }
        wf = _make_workflow(tmp_path, data)
        issues = validate_workflow(wf)
        assert any("name" in i for i in issues)

    def test_step_missing_agent(self, tmp_path: Path) -> None:
        data = {
            "name": "wf",
            "steps": [{"name": "step_a"}],
        }
        wf = _make_workflow(tmp_path, data)
        issues = validate_workflow(wf)
        assert any("agent" in i for i in issues)

    def test_duplicate_step_names(self, tmp_path: Path) -> None:
        data = {
            "name": "wf",
            "steps": [
                {"name": "build", "agent": "test/A/v1"},
                {"name": "build", "agent": "test/B/v1"},
            ],
        }
        wf = _make_workflow(tmp_path, data)
        issues = validate_workflow(wf)
        assert any("Duplicate" in i and "build" in i for i in issues)

    def test_dangling_depends_on(self, tmp_path: Path) -> None:
        data = {
            "name": "wf",
            "steps": [
                {"name": "step_a", "agent": "test/A/v1"},
                {"name": "step_b", "agent": "test/B/v1", "depends_on": ["nonexistent"]},
            ],
        }
        wf = _make_workflow(tmp_path, data)
        issues = validate_workflow(wf)
        assert any("nonexistent" in i for i in issues)

    def test_valid_depends_on_forward_reference(self, tmp_path: Path) -> None:
        """depends_on referencing a later step name should still resolve (all names pre-scanned)."""
        data = {
            "name": "wf",
            "steps": [
                {"name": "step_a", "agent": "test/A/v1", "depends_on": ["step_b"]},
                {"name": "step_b", "agent": "test/B/v1"},
            ],
        }
        wf = _make_workflow(tmp_path, data)
        issues = validate_workflow(wf)
        assert not any("nonexistent" in i or "not found" in i for i in issues)

    @pytest.mark.parametrize("on_fail", [
        "retry", "retry_fresh", "rollback", "abort",
        "escalate_human", "degrade", "fallback",
    ])
    def test_valid_gate_on_fail_values(self, tmp_path: Path, on_fail: str) -> None:
        data = {
            "name": "wf",
            "steps": [{"name": "s", "agent": "x/A/v1", "gate": {"on_fail": on_fail}}],
        }
        wf = _make_workflow(tmp_path, data)
        issues = validate_workflow(wf)
        assert not any("on_fail" in i for i in issues)

    def test_unknown_gate_on_fail(self, tmp_path: Path) -> None:
        data = {
            "name": "wf",
            "steps": [{"name": "s", "agent": "x/A/v1", "gate": {"on_fail": "explode"}}],
        }
        wf = _make_workflow(tmp_path, data)
        issues = validate_workflow(wf)
        assert any("explode" in i for i in issues)

    @pytest.mark.parametrize("gate_type", ["standard", "router", "approval"])
    def test_valid_gate_types(self, tmp_path: Path, gate_type: str) -> None:
        data = {
            "name": "wf",
            "steps": [{"name": "s", "agent": "x/A/v1", "gate": {"type": gate_type}}],
        }
        wf = _make_workflow(tmp_path, data)
        issues = validate_workflow(wf)
        assert not any("gate type" in i for i in issues)

    def test_unknown_gate_type(self, tmp_path: Path) -> None:
        data = {
            "name": "wf",
            "steps": [{"name": "s", "agent": "x/A/v1", "gate": {"type": "magic"}}],
        }
        wf = _make_workflow(tmp_path, data)
        issues = validate_workflow(wf)
        assert any("magic" in i for i in issues)

    def test_feedback_loop_from_step_not_found(self, tmp_path: Path) -> None:
        data = {
            "name": "wf",
            "steps": [{"name": "s", "agent": "x/A/v1"}],
            "feedback_loops": [{"from_step": "ghost", "to_step": "s"}],
        }
        wf = _make_workflow(tmp_path, data)
        issues = validate_workflow(wf)
        assert any("from_step" in i and "ghost" in i for i in issues)

    def test_feedback_loop_to_step_not_found(self, tmp_path: Path) -> None:
        data = {
            "name": "wf",
            "steps": [{"name": "s", "agent": "x/A/v1"}],
            "feedback_loops": [{"from_step": "s", "to_step": "phantom"}],
        }
        wf = _make_workflow(tmp_path, data)
        issues = validate_workflow(wf)
        assert any("to_step" in i and "phantom" in i for i in issues)

    def test_valid_feedback_loop(self, tmp_path: Path) -> None:
        data = {
            "name": "wf",
            "steps": [
                {"name": "build", "agent": "x/A/v1"},
                {"name": "test", "agent": "x/B/v1", "depends_on": ["build"]},
            ],
            "feedback_loops": [{"from_step": "test", "to_step": "build", "max_iterations": 2}],
        }
        wf = _make_workflow(tmp_path, data)
        assert validate_workflow(wf) == []

    def test_step_not_a_dict(self, tmp_path: Path) -> None:
        data = {"name": "wf", "steps": ["not_a_dict"]}
        wf = _make_workflow(tmp_path, data)
        issues = validate_workflow(wf)
        assert any("not a mapping" in i for i in issues)

    def test_steps_not_a_list(self, tmp_path: Path) -> None:
        data = {"name": "wf", "steps": "single_step"}
        wf = _make_workflow(tmp_path, data)
        issues = validate_workflow(wf)
        assert any("steps" in i and "list" in i for i in issues)

    def test_workflow_root_not_dict(self, tmp_path: Path) -> None:
        wf = tmp_path / "wf.yaml"
        wf.write_text("- item\n", encoding="utf-8")
        issues = validate_workflow(wf)
        assert any("mapping" in i for i in issues)

    def test_real_world_feature_delivery_workflow(self) -> None:
        """The bundled feature_delivery.yaml must be valid."""
        wf = Path(__file__).resolve().parent.parent.parent / "workflows" / "cross_domain" / "feature_delivery.yaml"
        if not wf.exists():
            pytest.skip("feature_delivery.yaml not present")
        assert validate_workflow(wf) == []

    def test_real_world_quality_scoring_workflow(self) -> None:
        """The bundled quality_scoring.yaml must be valid."""
        wf = Path(__file__).resolve().parent.parent.parent / "workflows" / "maintenance" / "quality_scoring.yaml"
        if not wf.exists():
            pytest.skip("quality_scoring.yaml not present")
        assert validate_workflow(wf) == []


# ── CLI integration ────────────────────────────────────────────────────────────


class TestValidateCLI:
    """Light smoke tests for the extended `ai validate` CLI command."""

    def test_validate_agent_dir_cli(self, tmp_path: Path) -> None:
        from click.testing import CliRunner
        from harness.cli.ai import cli

        agent_dir = _make_agent(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", "--agent", str(agent_dir)])
        assert result.exit_code == 0, result.output
        assert "valid" in result.output

    def test_validate_workflow_cli(self, tmp_path: Path) -> None:
        from click.testing import CliRunner
        from harness.cli.ai import cli

        wf = _make_workflow(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", "--workflow", str(wf)])
        assert result.exit_code == 0, result.output
        assert "valid" in result.output

    def test_validate_broken_agent_exits_nonzero(self, tmp_path: Path) -> None:
        from click.testing import CliRunner
        from harness.cli.ai import cli

        # Agent dir with no manifest
        bad_dir = tmp_path / "BadAgent" / "v1"
        bad_dir.mkdir(parents=True)
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", "--agent", str(bad_dir)])
        assert result.exit_code != 0

    def test_validate_positional_agent_dir(self, tmp_path: Path) -> None:
        from click.testing import CliRunner
        from harness.cli.ai import cli

        agent_dir = _make_agent(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(agent_dir)])
        assert result.exit_code == 0, result.output

    def test_validate_positional_workflow_yaml(self, tmp_path: Path) -> None:
        from click.testing import CliRunner
        from harness.cli.ai import cli

        wf = _make_workflow(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(wf)])
        assert result.exit_code == 0, result.output

    def test_validate_domain_with_agents_and_workflows(self, tmp_path: Path) -> None:
        from click.testing import CliRunner
        from harness.cli.ai import cli

        # Build a minimal domain tree
        agent_dir = tmp_path / "agents" / "MyAgent" / "v1"
        agent_dir.mkdir(parents=True)
        _write(agent_dir / "agent_manifest.yaml", {
            "id": "test/MyAgent/v1", "domain": "test",
            "category": "reasoning", "description": "x",
        })
        (agent_dir / "system_prompt.md").write_text("prompt", encoding="utf-8")

        wf_dir = tmp_path / "workflows"
        wf_dir.mkdir()
        _write(wf_dir / "simple.yaml", {
            "name": "simple",
            "steps": [{"name": "s", "agent": "test/MyAgent/v1"}],
        })

        runner = CliRunner()
        result = runner.invoke(cli, ["validate", "--domain", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "valid" in result.output
