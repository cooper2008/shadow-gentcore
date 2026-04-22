"""AgentRunner → citation_checker wiring tests.

Proves the runner correctly:
  - attaches `_citation_report` + `citation_score` + `citation_passed`
    scalars to every completed result
  - reads `manifest.citations.min` + `require_tiers` to parametrise scoring
  - continues cleanly when citations config is absent or citation scoring
    fails internally
  - surfaces scalars that gate expressions can use
"""

from __future__ import annotations

import json

import pytest

from harness.core.agent_runner import AgentRunner
from harness.core.expr import evaluate


class _FakeProvider:
    """Minimal chat provider that returns a canned JSON payload."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def chat(self, messages, **kw):
        return {
            "content": json.dumps(self._payload),
            "tokens_used": 10,
            "tool_calls": [],
        }


@pytest.mark.asyncio
async def test_citation_report_attached_on_completed_run():
    provider = _FakeProvider({
        "summary": "done",
        "citations": [
            {"claim": "use APIRouter", "tier": "T2", "source": "fastapi_routers"},
        ],
    })
    runner = AgentRunner(provider=provider)
    result = await runner.run(
        manifest={"id": "x/A/v1", "tools": []},
        task={},
        system_prompt_content="prompt",
    )
    assert result["status"] == "completed"
    assert "citation_score" in result
    assert "citation_passed" in result
    assert "_citation_report" in result
    report = result["_citation_report"]
    assert report["total_claims"] == 1
    assert report["cited_claims"] == 1


@pytest.mark.asyncio
async def test_require_tiers_enforced_via_manifest():
    provider = _FakeProvider({
        "summary": "done",
        "citations": [
            {"claim": "stds", "tier": "T1", "source": "standards"},
        ],
    })
    runner = AgentRunner(provider=provider)
    result = await runner.run(
        manifest={"id": "x/A/v1", "tools": [],
                  "citations": {"min": 1, "require_tiers": ["T2"]}},
        task={}, system_prompt_content="prompt",
    )
    # T2 not cited → finding → not passed
    report = result["_citation_report"]
    assert report["passed"] is False
    assert any("Required tier T2" in f["message"] for f in report["findings"])
    assert result["citation_passed"] is False


@pytest.mark.asyncio
async def test_gate_expression_uses_citation_score():
    """Verify the real end-to-end usage: gate expression reads citation_score."""
    provider = _FakeProvider({
        "summary": "done",
        "citations": [
            {"claim": "a", "tier": "T2", "source": "chunk_1"},
            {"claim": "b", "tier": "T1", "source": "standards"},
            {"claim": "c", "tier": "T3", "source": "src/app.py"},
        ],
    })
    runner = AgentRunner(provider=provider)
    result = await runner.run(
        manifest={"id": "x/A/v1", "tools": []},
        task={}, system_prompt_content="prompt",
    )
    # Full coverage → score should be 1.0
    assert result["citation_score"] == 1.0
    gate = "status == completed and citation_score >= 0.75"
    assert evaluate(gate, result) is True


@pytest.mark.asyncio
async def test_no_citations_no_manifest_config_neutral():
    """Agents that don't emit citations don't break — score defaults neutral."""
    provider = _FakeProvider({"summary": "just a status ping, no facts"})
    runner = AgentRunner(provider=provider)
    result = await runner.run(
        manifest={"id": "x/A/v1", "tools": []},
        task={}, system_prompt_content="prompt",
    )
    # Zero citations emitted, no min threshold → neutral 1.0
    assert result["citation_score"] == 1.0
    assert result["citation_passed"] is True


@pytest.mark.asyncio
async def test_min_citations_threshold_blocks():
    provider = _FakeProvider({"summary": "missing citations"})
    runner = AgentRunner(provider=provider)
    result = await runner.run(
        manifest={"id": "x/A/v1", "tools": [],
                  "citations": {"min": 2}},
        task={}, system_prompt_content="prompt",
    )
    # No citations but min=2 demanded → score 0.0, passed False
    assert result["citation_score"] == 0.0
    assert result["citation_passed"] is False


@pytest.mark.asyncio
async def test_malformed_citations_in_output_graceful():
    """Malformed output must not crash the runner — citation step is
    defensive and falls through."""
    provider = _FakeProvider({"citations": "not a list"})
    runner = AgentRunner(provider=provider)
    result = await runner.run(
        manifest={"id": "x/A/v1", "tools": []},
        task={}, system_prompt_content="prompt",
    )
    # Must still complete; report attached with 0 citations.
    assert result["status"] == "completed"
    assert result["_citation_report"]["total_claims"] == 0
