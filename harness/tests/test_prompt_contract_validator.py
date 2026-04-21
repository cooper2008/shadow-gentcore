"""Unit tests for prompt ↔ manifest contract validator."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.core.prompt_contract_validator import (
    ContractFinding,
    ContractReport,
    validate_agent_contract,
    validate_domain_contracts,
)


def _build_agent(tmp_path: Path, name: str, manifest: dict, prompt: str) -> Path:
    """Scaffold an agent dir with manifest + prompt; returns manifest path."""
    agent_dir = tmp_path / "agents" / name / "v1"
    agent_dir.mkdir(parents=True)
    manifest_path = agent_dir / "agent_manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest))
    (agent_dir / "system_prompt.md").write_text(prompt)
    return manifest_path


class TestIdentityCheck:
    def test_matching_identity_passes(self, tmp_path):
        mp = _build_agent(tmp_path, "TriageAgent",
            {"id": "acme/TriageAgent/v1", "tools": []},
            "You are TriageAgent — triage incidents.")
        findings = validate_agent_contract(mp)
        assert not any(f.rule_id == "identity-mismatch" for f in findings)

    def test_mismatched_identity_errors(self, tmp_path):
        mp = _build_agent(tmp_path, "TriageAgent",
            {"id": "acme/TriageAgent/v1", "tools": []},
            "You are NotifierAgent.")
        findings = validate_agent_contract(mp)
        errors = [f for f in findings if f.rule_id == "identity-mismatch"]
        assert errors and errors[0].severity == "error"


class TestToolConsistency:
    def test_undeclared_tool_referenced_warn(self, tmp_path):
        mp = _build_agent(tmp_path, "SummAgent",
            {"id": "acme/SummAgent/v1", "tools": []},
            "You are SummAgent. Call `context_retrieve(topic)` to fetch chunks.")
        findings = validate_agent_contract(mp)
        assert any(f.rule_id == "prompt-mentions-undeclared-tool" for f in findings)

    def test_declared_tool_no_warn(self, tmp_path):
        mp = _build_agent(tmp_path, "SummAgent",
            {"id": "acme/SummAgent/v1",
             "tools": [{"name": "context_retrieve"}]},
            "You are SummAgent. Call `context_retrieve(topic)`.")
        findings = validate_agent_contract(mp)
        assert not any(f.rule_id == "prompt-mentions-undeclared-tool" for f in findings)

    def test_generic_identifier_ignored(self, tmp_path):
        # `content` is generic — should NOT be flagged as undeclared tool
        mp = _build_agent(tmp_path, "Ag",
            {"id": "x/Ag/v1", "tools": []},
            "You are Ag. Emit `content` in your output.")
        findings = validate_agent_contract(mp)
        assert not any(f.rule_id == "prompt-mentions-undeclared-tool" for f in findings)


class TestOutputSchemaFieldRefs:
    def test_undeclared_field_warn(self, tmp_path):
        mp = _build_agent(tmp_path, "Ag",
            {"id": "x/Ag/v1", "tools": [],
             "output_schema": {
                 "type": "object",
                 "properties": {"files": {"type": "array"}},
             }},
            "You are Ag. Emit `output.agents_created[]`.")
        findings = validate_agent_contract(mp)
        assert any(f.rule_id == "prompt-refs-undeclared-output-field" for f in findings)

    def test_declared_field_passes(self, tmp_path):
        mp = _build_agent(tmp_path, "Ag",
            {"id": "x/Ag/v1", "tools": [],
             "output_schema": {"type": "object", "properties": {"files": {}}}},
            "You are Ag. Emit `output.files`.")
        findings = validate_agent_contract(mp)
        assert not any(f.rule_id == "prompt-refs-undeclared-output-field" for f in findings)


class TestExecutionMode:
    def test_single_turn_claim_with_high_steps_warn(self, tmp_path):
        mp = _build_agent(tmp_path, "Ag",
            {"id": "x/Ag/v1", "tools": [],
             "execution_mode": {"primary": "react", "max_react_steps": 10}},
            "You are Ag. You operate in single-turn mode.")
        findings = validate_agent_contract(mp)
        assert any(f.rule_id == "prompt-claims-single-shot-but-max-steps-gt-1" for f in findings)

    def test_single_turn_claim_consistent(self, tmp_path):
        mp = _build_agent(tmp_path, "Ag",
            {"id": "x/Ag/v1", "tools": [],
             "execution_mode": {"primary": "react", "max_react_steps": 1}},
            "You are Ag. You operate in single-turn mode.")
        findings = validate_agent_contract(mp)
        assert not any(f.rule_id == "prompt-claims-single-shot-but-max-steps-gt-1" for f in findings)

    def test_multi_turn_claim_no_false_positive(self, tmp_path):
        mp = _build_agent(tmp_path, "Ag",
            {"id": "x/Ag/v1", "tools": [],
             "execution_mode": {"primary": "react", "max_react_steps": 10}},
            "You are Ag. Use react mode to iterate.")
        findings = validate_agent_contract(mp)
        assert not any(f.rule_id == "prompt-claims-single-shot-but-max-steps-gt-1" for f in findings)


class TestPreloadReferences:
    def test_undeclared_preload_warn(self, tmp_path):
        mp = _build_agent(tmp_path, "Ag",
            {"id": "x/Ag/v1", "tools": [], "context": {"preload": []}},
            "You are Ag. Uses `preload:tool_pack_catalog`.")
        findings = validate_agent_contract(mp)
        assert any(f.rule_id == "prompt-references-undeclared-preload" for f in findings)

    def test_declared_preload_passes(self, tmp_path):
        mp = _build_agent(tmp_path, "Ag",
            {"id": "x/Ag/v1", "tools": [],
             "context": {"preload": ["tool_pack_catalog"]}},
            "You are Ag. Uses `preload:tool_pack_catalog`.")
        findings = validate_agent_contract(mp)
        assert not any(f.rule_id == "prompt-references-undeclared-preload" for f in findings)


class TestMemoryTierConsistency:
    def test_tier2_taught_but_tool_missing_warn(self, tmp_path):
        mp = _build_agent(tmp_path, "Ag",
            {"id": "x/Ag/v1", "tools": []},
            "You are Ag. When you need reference info, call "
            "`context_retrieve(topic, keywords)`.")
        findings = validate_agent_contract(mp)
        assert any(f.rule_id == "memory-tier2-tool-not-declared" for f in findings)

    def test_tier2_with_tool_declared_passes(self, tmp_path):
        mp = _build_agent(tmp_path, "Ag",
            {"id": "x/Ag/v1",
             "tools": [{"name": "context_retrieve"}]},
            "You are Ag. Use `context_retrieve(topic, keywords)`.")
        findings = validate_agent_contract(mp)
        assert not any(f.rule_id == "memory-tier2-tool-not-declared" for f in findings)

    def test_tier3_taught_but_missing_warn(self, tmp_path):
        mp = _build_agent(tmp_path, "Ag",
            {"id": "x/Ag/v1", "tools": []},
            "You are Ag. Fall back to `origin_fetch(path)` when chunks miss.")
        findings = validate_agent_contract(mp)
        assert any(f.rule_id == "memory-tier3-tool-not-declared" for f in findings)


class TestDomainLevelReport:
    def test_domain_report_aggregates(self, tmp_path):
        _build_agent(tmp_path, "GoodAgent",
            {"id": "x/GoodAgent/v1",
             "tools": [{"name": "context_retrieve"}]},
            "You are GoodAgent. Use `context_retrieve(topic, keywords)`.")
        _build_agent(tmp_path, "BadAgent",
            {"id": "x/BadAgent/v1", "tools": []},
            "You are BadAgent. Call `context_retrieve(topic, keywords)`.")
        report = validate_domain_contracts(tmp_path)
        assert report.agents_checked == 2
        # Bad agent should produce at least 1 warn
        assert any(f.agent_id == "x/BadAgent/v1" for f in report.findings)

    def test_clean_domain_passes(self, tmp_path):
        _build_agent(tmp_path, "Ag",
            {"id": "x/Ag/v1", "tools": [{"name": "context_retrieve"}]},
            "You are Ag. Use `context_retrieve(topic, keywords)`.")
        report = validate_domain_contracts(tmp_path)
        assert report.passed is True  # no errors
        assert "aligned" in report.format_cli() or report.agents_checked == 1


class TestMissingPromptFile:
    def test_missing_prompt_raises_error(self, tmp_path):
        agent_dir = tmp_path / "agents" / "X" / "v1"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent_manifest.yaml").write_text(yaml.safe_dump({
            "id": "x/X/v1", "tools": [],
        }))
        # Don't write system_prompt.md
        findings = validate_agent_contract(agent_dir / "agent_manifest.yaml")
        assert any(f.rule_id == "prompt-missing" and f.severity == "error"
                   for f in findings)


class TestReportFormatting:
    def test_format_cli_lists_findings(self):
        report = ContractReport(
            findings=[ContractFinding(
                rule_id="test-rule", severity="warn",
                agent_id="x/Y/v1", location="test", message="msg",
            )],
            agents_checked=1,
        )
        out = report.format_cli()
        assert "test-rule" in out
        assert "WARN" in out

    def test_format_cli_green_when_empty(self):
        report = ContractReport(agents_checked=3)
        assert "aligned" in report.format_cli()
