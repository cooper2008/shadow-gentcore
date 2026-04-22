"""Tests for the LLM-as-judge layer of the prompt contract validator.

The judge is opt-in and advisory by default. These tests verify:
  * Regex findings remain unchanged when judge runs
  * Judge findings get severity="advisory" unless corroborated
  * Corroborated findings (rule_id matches a regex WARN) upgrade to "warn"
  * Provider failures degrade gracefully (skip judge, keep regex)
  * Unparseable judge output is rejected cleanly
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from harness.core.prompt_contract_validator import (
    ContractFinding,
    validate_agent_contract,
    validate_agent_contract_with_judge,
)


def _build_agent(tmp_path: Path, name: str, manifest: dict, prompt: str) -> Path:
    agent_dir = tmp_path / "agents" / name / "v1"
    agent_dir.mkdir(parents=True)
    mp = agent_dir / "agent_manifest.yaml"
    mp.write_text(yaml.safe_dump(manifest))
    (agent_dir / "system_prompt.md").write_text(prompt)
    return mp


def _fake_provider(findings: list[dict] | None = None, fail: bool = False):
    """Build a mock provider whose chat() returns structured judge findings."""
    payload = {"findings": findings or []}
    class _Resp:
        content = json.dumps(payload)
    provider = AsyncMock()
    if fail:
        provider.chat = AsyncMock(side_effect=Exception("provider boom"))
    else:
        provider.chat = AsyncMock(return_value=_Resp())
    return provider


class TestJudgeRunsAlongsideRegex:
    @pytest.mark.asyncio
    async def test_no_provider_returns_regex_only(self, tmp_path):
        mp = _build_agent(tmp_path, "Clean",
            {"id": "x/Clean/v1", "tools": []},
            "You are Clean.")
        out = await validate_agent_contract_with_judge(mp, provider=None)
        # Regex-only findings (clean agent → zero)
        assert out == []

    @pytest.mark.asyncio
    async def test_judge_findings_are_advisory(self, tmp_path):
        mp = _build_agent(tmp_path, "TriageAgent",
            {"id": "x/TriageAgent/v1", "tools": []},
            "You are TriageAgent.")
        provider = _fake_provider(findings=[
            {"rule_id": "judge-persona-drift", "severity": "advisory",
             "location": "prompt", "message": "Persona too brief", "evidence": "You are TriageAgent."},
        ])
        out = await validate_agent_contract_with_judge(mp, provider=provider)
        judge = [f for f in out if f.rule_id.startswith("judge-")]
        assert len(judge) == 1
        assert judge[0].severity == "advisory"

    @pytest.mark.asyncio
    async def test_judge_rule_id_auto_prefixed(self, tmp_path):
        """Judge may return raw rule_ids — validator prepends 'judge-'."""
        mp = _build_agent(tmp_path, "A",
            {"id": "x/A/v1", "tools": []}, "You are A.")
        provider = _fake_provider(findings=[
            {"rule_id": "persona-mismatch", "severity": "warn",
             "message": "msg", "location": "l"},
        ])
        out = await validate_agent_contract_with_judge(mp, provider=provider)
        judge = [f for f in out if f.rule_id.startswith("judge-")]
        assert len(judge) == 1
        assert judge[0].rule_id == "judge-persona-mismatch"


class TestCorroborationUpgrades:
    @pytest.mark.asyncio
    async def test_judge_corroboration_upgrades_to_warn(self, tmp_path):
        """Judge finding whose rule_id overlaps a regex WARN → severity warn."""
        # Agent with tool-mention drift (regex will WARN)
        mp = _build_agent(tmp_path, "A",
            {"id": "x/A/v1", "tools": []},
            "You are A. Call `context_retrieve(topic)` to fetch chunks.")
        # Judge emits a finding with same rule_id AS THE REGEX WARN emitted.
        # (The regex will emit rule_id="prompt-mentions-undeclared-tool")
        provider = _fake_provider(findings=[
            {"rule_id": "prompt-mentions-undeclared-tool", "severity": "advisory",
             "message": "Judge also flagged this — confirms drift",
             "location": "prompt"},
        ])
        out = await validate_agent_contract_with_judge(mp, provider=provider)
        judge_hits = [f for f in out if f.rule_id.startswith("judge-")]
        assert len(judge_hits) == 1
        # Corroboration upgrade: advisory → warn
        assert judge_hits[0].severity == "warn"
        assert "corroborated" in judge_hits[0].message.lower()


class TestJudgeFailureDegradation:
    @pytest.mark.asyncio
    async def test_provider_exception_returns_regex_only(self, tmp_path):
        mp = _build_agent(tmp_path, "Clean",
            {"id": "x/Clean/v1", "tools": []},
            "You are Clean.")
        provider = _fake_provider(fail=True)
        out = await validate_agent_contract_with_judge(mp, provider=provider)
        # Judge failed silently; regex found nothing.
        assert not any(f.rule_id.startswith("judge-") for f in out)

    @pytest.mark.asyncio
    async def test_unparseable_judge_output_rejected(self, tmp_path):
        mp = _build_agent(tmp_path, "Clean",
            {"id": "x/Clean/v1", "tools": []},
            "You are Clean.")

        class _BadResp:
            content = "not json"
        provider = AsyncMock()
        provider.chat = AsyncMock(return_value=_BadResp())
        out = await validate_agent_contract_with_judge(mp, provider=provider)
        assert not any(f.rule_id.startswith("judge-") for f in out)

    @pytest.mark.asyncio
    async def test_unknown_severity_defaulted_to_advisory(self, tmp_path):
        mp = _build_agent(tmp_path, "A",
            {"id": "x/A/v1", "tools": []}, "You are A.")
        provider = _fake_provider(findings=[
            {"rule_id": "x", "severity": "critical",   # not in enum
             "message": "msg", "location": "l"},
        ])
        out = await validate_agent_contract_with_judge(mp, provider=provider)
        judge = [f for f in out if f.rule_id.startswith("judge-")]
        assert judge and judge[0].severity == "advisory"


class TestRegexResultsStillPresent:
    """Judge MUST NOT swallow regex findings — the two layers compose."""

    @pytest.mark.asyncio
    async def test_regex_warn_preserved(self, tmp_path):
        mp = _build_agent(tmp_path, "A",
            {"id": "x/A/v1", "tools": []},
            "You are A. Call `memory_recall(k=5)` to get memory.")
        provider = _fake_provider(findings=[])  # empty judge
        out = await validate_agent_contract_with_judge(mp, provider=provider)
        regex_warns = [f for f in out if f.severity == "warn"]
        assert any(f.rule_id == "memory-tier4-tool-not-declared" for f in regex_warns)
