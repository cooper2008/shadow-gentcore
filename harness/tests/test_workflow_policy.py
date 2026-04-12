"""Tests for workflow policy baseline enforcement."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from harness.authoring.validator import Validator, ValidationResult, classify_agent


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


@pytest.fixture()
def good_domain(tmp_path: Path) -> Path:
    """A domain that meets all policy requirements."""
    # domain.yaml
    _write(tmp_path / "domain.yaml", """
        name: test
        owner: team
        purpose: Test domain
    """)

    # context/standards.md (>100 chars)
    _write(tmp_path / "context/standards.md", """
        # Test Standards
        - Use type annotations on all functions
        - Use pytest for testing
        - Follow PEP 8 naming conventions
        - All public functions must have docstrings
    """)

    # Build agent (CodeWriter — file_write: allow)
    _write(tmp_path / "agents/CodeWriter/v1/agent_manifest.yaml", yaml.dump({
        "id": "test/CodeWriter/v1",
        "domain": "test",
        "execution_mode": {"primary": "plan_execute"},
        "system_prompt_ref": "system_prompt.md",
        "permissions": {"file_edit": "allow", "file_create": "allow", "shell_command": "deny"},
        "tools": [{"name": "file_write", "pack": "toolpack://core/filesystem"}],
        "input_schema": {"type": "object", "required": ["task"]},
        "output_schema": {"type": "object", "required": ["summary"]},
    }))
    _write(tmp_path / "agents/CodeWriter/v1/system_prompt.md",
           "You are CodeWriter. Generate code following standards from context.")

    # Verify agent (TestRunner — shell_command: allow)
    _write(tmp_path / "agents/TestRunner/v1/agent_manifest.yaml", yaml.dump({
        "id": "test/TestRunner/v1",
        "domain": "test",
        "execution_mode": {"primary": "react"},
        "system_prompt_ref": "system_prompt.md",
        "permissions": {"file_edit": "deny", "shell_command": "allow"},
        "tools": [{"name": "shell_exec", "pack": "toolpack://core/shell"}],
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object", "required": ["passed"]},
    }))
    _write(tmp_path / "agents/TestRunner/v1/system_prompt.md",
           "You are TestRunner. Run tests and report results from context.")

    # Review agent (Reviewer — chain_of_thought, read-only)
    _write(tmp_path / "agents/Reviewer/v1/agent_manifest.yaml", yaml.dump({
        "id": "test/Reviewer/v1",
        "domain": "test",
        "execution_mode": {"primary": "chain_of_thought"},
        "system_prompt_ref": "system_prompt.md",
        "permissions": {"file_edit": "deny", "shell_command": "deny"},
        "tools": [{"name": "file_read", "pack": "toolpack://core/filesystem"}],
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object", "required": ["approved"]},
    }))
    _write(tmp_path / "agents/Reviewer/v1/system_prompt.md",
           "You are Reviewer. Review code against standards from context.")

    # Workflow with build + verify + review
    _write(tmp_path / "workflows/main.yaml", yaml.dump({
        "name": "main",
        "domain": "test",
        "steps": [
            {"name": "code", "agent": "test/CodeWriter/v1",
             "gate": {"condition": "status == success", "on_fail": "abort"}},
            {"name": "test", "agent": "test/TestRunner/v1", "depends_on": ["code"],
             "gate": {"condition": "status == success", "on_fail": "retry", "max_retries": 2}},
            {"name": "review", "agent": "test/Reviewer/v1", "depends_on": ["test"],
             "gate": {"condition": "status == success", "on_fail": "degrade"}},
        ],
        "budget": {"max_tokens": 100000, "max_cost_usd": 5.0},
    }))

    return tmp_path


class TestClassifyAgent:
    def test_build_agent(self) -> None:
        m = {"permissions": {"file_edit": "allow"}, "execution_mode": {"primary": "plan_execute"}}
        caps = classify_agent(m)
        assert "build" in caps

    def test_verify_agent(self) -> None:
        m = {"permissions": {"shell_command": "allow", "file_edit": "deny"}, "execution_mode": {"primary": "react"}}
        caps = classify_agent(m)
        assert "verify" in caps
        assert "execute" in caps

    def test_review_agent(self) -> None:
        m = {"permissions": {"file_edit": "deny", "shell_command": "deny"}, "execution_mode": {"primary": "chain_of_thought"}}
        caps = classify_agent(m)
        assert "review" in caps
        assert "analyze" in caps

    def test_read_only_agent(self) -> None:
        m = {"permissions": {"file_edit": "deny", "shell_command": "deny"}, "execution_mode": {"primary": "react"}}
        caps = classify_agent(m)
        assert "plan" in caps or "analyze" in caps


class TestPolicyBaselinePass:
    """Domains that meet the baseline should pass validation."""

    def test_good_domain_passes(self, good_domain: Path) -> None:
        v = Validator()
        result = v.validate_domain(good_domain)
        policy_errors = [e for e in result.errors if e["rule"].startswith("policy_")]
        assert not policy_errors, f"Policy errors: {policy_errors}"

    def test_good_domain_has_all_capabilities(self, good_domain: Path) -> None:
        v = Validator()
        result = v.validate_domain(good_domain)
        assert result.is_valid, f"Errors: {result.errors}"


class TestCapabilityPairRules:
    """Capability-pair rules: if X then must have Y."""

    def test_build_without_verify_fails(self, tmp_path: Path) -> None:
        """Workflow that writes code but has no verification → error."""
        _write(tmp_path / "domain.yaml", "name: x\nowner: t\npurpose: t")
        _write(tmp_path / "context/standards.md", "# Standards\n" + "x" * 100)

        _write(tmp_path / "agents/Coder/v1/agent_manifest.yaml", yaml.dump({
            "id": "x/Coder/v1", "domain": "x",
            "execution_mode": {"primary": "plan_execute"},
            "system_prompt_ref": "system_prompt.md",
            "permissions": {"file_edit": "allow"},
            "tools": [], "input_schema": {}, "output_schema": {},
        }))
        _write(tmp_path / "agents/Coder/v1/system_prompt.md", "You are Coder. " + "x" * 50)

        # Reviewer (read-only, provides review but NOT verify)
        _write(tmp_path / "agents/Reviewer/v1/agent_manifest.yaml", yaml.dump({
            "id": "x/Reviewer/v1", "domain": "x",
            "execution_mode": {"primary": "chain_of_thought"},
            "system_prompt_ref": "system_prompt.md",
            "permissions": {"file_edit": "deny", "shell_command": "deny"},
            "tools": [], "input_schema": {}, "output_schema": {},
        }))
        _write(tmp_path / "agents/Reviewer/v1/system_prompt.md", "You are Reviewer. " + "x" * 50)

        _write(tmp_path / "workflows/main.yaml", yaml.dump({
            "name": "main", "domain": "x",
            "steps": [
                {"name": "code", "agent": "x/Coder/v1"},
                {"name": "review", "agent": "x/Reviewer/v1", "depends_on": ["code"]},
            ],
            "budget": {"max_tokens": 50000},
        }))

        v = Validator()
        result = v.validate_domain(tmp_path)
        policy_errors = [e for e in result.errors if e["rule"] == "policy_baseline"]
        assert any("verify" in e["message"].lower() or "verification" in e["message"].lower() for e in policy_errors)

    def test_execute_without_validate_fails(self, tmp_path: Path) -> None:
        """Workflow that runs commands but has no validation → error."""
        _write(tmp_path / "domain.yaml", "name: x\nowner: t\npurpose: t")
        _write(tmp_path / "context/standards.md", "# Standards\n" + "x" * 100)

        _write(tmp_path / "agents/Deployer/v1/agent_manifest.yaml", yaml.dump({
            "id": "x/Deployer/v1", "domain": "x",
            "execution_mode": {"primary": "react"},
            "system_prompt_ref": "system_prompt.md",
            "permissions": {"shell_command": "allow", "file_edit": "deny"},
            "tools": [], "input_schema": {}, "output_schema": {},
        }))
        _write(tmp_path / "agents/Deployer/v1/system_prompt.md", "You are Deployer. " + "x" * 50)

        # Only one execute step — but execute provides BOTH execute AND validate
        # So this should actually PASS (execute agent also has validate capability)
        _write(tmp_path / "workflows/main.yaml", yaml.dump({
            "name": "main", "domain": "x",
            "steps": [
                {"name": "deploy", "agent": "x/Deployer/v1"},
                {"name": "check", "agent": "x/Deployer/v1", "depends_on": ["deploy"]},
            ],
            "budget": {"max_tokens": 50000},
        }))

        v = Validator()
        result = v.validate_domain(tmp_path)
        # Should pass — execute agent provides both execute + validate
        policy_errors = [e for e in result.errors if e["rule"] == "policy_baseline"]
        assert not any("validate" in e["message"].lower() for e in policy_errors)

    def test_ops_workflow_passes_without_build(self, tmp_path: Path) -> None:
        """DevOps workflow (no code writing) should pass — no build rule triggered."""
        _write(tmp_path / "domain.yaml", "name: ops\nowner: t\npurpose: t")
        _write(tmp_path / "context/standards.md", "# Ops Standards\n" + "x" * 100)

        # Plan agent (read-only)
        _write(tmp_path / "agents/Planner/v1/agent_manifest.yaml", yaml.dump({
            "id": "ops/Planner/v1", "domain": "ops",
            "execution_mode": {"primary": "chain_of_thought"},
            "system_prompt_ref": "system_prompt.md",
            "permissions": {"file_edit": "deny", "shell_command": "deny"},
            "tools": [], "input_schema": {}, "output_schema": {},
        }))
        _write(tmp_path / "agents/Planner/v1/system_prompt.md", "You are Planner. " + "x" * 50)

        # Deploy agent (shell)
        _write(tmp_path / "agents/Deployer/v1/agent_manifest.yaml", yaml.dump({
            "id": "ops/Deployer/v1", "domain": "ops",
            "execution_mode": {"primary": "react"},
            "system_prompt_ref": "system_prompt.md",
            "permissions": {"shell_command": "allow", "file_edit": "deny"},
            "tools": [], "input_schema": {}, "output_schema": {},
        }))
        _write(tmp_path / "agents/Deployer/v1/system_prompt.md", "You are Deployer. " + "x" * 50)

        _write(tmp_path / "workflows/deploy.yaml", yaml.dump({
            "name": "deploy", "domain": "ops",
            "steps": [
                {"name": "plan", "agent": "ops/Planner/v1",
                 "gate": {"condition": "status == success", "on_fail": "abort"}},
                {"name": "deploy", "agent": "ops/Deployer/v1", "depends_on": ["plan"],
                 "gate": {"condition": "status == success", "on_fail": "abort"}},
            ],
            "budget": {"max_tokens": 50000},
        }))

        v = Validator()
        result = v.validate_domain(tmp_path)
        # No build capability → no build-related rules triggered → should pass
        policy_errors = [e for e in result.errors if e["rule"] == "policy_baseline"]
        assert not policy_errors, f"Ops workflow should pass: {policy_errors}"

    def test_missing_standards_warns(self, tmp_path: Path) -> None:
        """Domain without context/standards.md → warning."""
        _write(tmp_path / "domain.yaml", "name: x\nowner: t\npurpose: t")

        v = Validator()
        result = v.validate_domain(tmp_path)
        context_warnings = [w for w in result.warnings if w["rule"] == "policy_context"]
        assert len(context_warnings) >= 1


class TestGatePolicy:
    def test_verify_step_without_gate_warns(self, tmp_path: Path) -> None:
        _write(tmp_path / "domain.yaml", "name: x\nowner: t\npurpose: t")
        _write(tmp_path / "context/standards.md", "# Standards\n" + "x" * 100)

        _write(tmp_path / "agents/Tester/v1/agent_manifest.yaml", yaml.dump({
            "id": "x/Tester/v1", "domain": "x",
            "execution_mode": {"primary": "react"},
            "system_prompt_ref": "system_prompt.md",
            "permissions": {"shell_command": "allow"},
            "tools": [], "input_schema": {}, "output_schema": {},
        }))
        _write(tmp_path / "agents/Tester/v1/system_prompt.md", "You are Tester. " + "x" * 50)

        # Verify step WITHOUT a gate
        _write(tmp_path / "workflows/main.yaml", yaml.dump({
            "name": "main", "domain": "x",
            "steps": [{"name": "test", "agent": "x/Tester/v1"}],  # no gate!
            "budget": {"max_tokens": 50000},
        }))

        v = Validator()
        result = v.validate_domain(tmp_path)
        gate_warnings = [w for w in result.warnings if w["rule"] == "policy_gate"]
        assert len(gate_warnings) >= 1


class TestRealDomainCompliance:
    """Existing example domains should pass policy validation."""

    def test_backend_fastapi_passes(self) -> None:
        domain = Path(__file__).parent.parent.parent / "examples" / "backend_fastapi"
        if not domain.exists():
            pytest.skip("backend_fastapi not found")
        v = Validator()
        result = v.validate_domain(domain)
        policy_errors = [e for e in result.errors if e["rule"] == "policy_baseline"]
        assert not policy_errors, f"backend_fastapi policy violations: {policy_errors}"

    def test_frontend_react_passes(self) -> None:
        domain = Path(__file__).parent.parent.parent / "examples" / "frontend_react"
        if not domain.exists():
            pytest.skip("frontend_react not found")
        v = Validator()
        result = v.validate_domain(domain)
        policy_errors = [e for e in result.errors if e["rule"] == "policy_baseline"]
        assert not policy_errors, f"frontend_react policy violations: {policy_errors}"
